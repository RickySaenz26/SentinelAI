from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.api.v1.schemas import RecoveryConfirmRequest, RecoveryRequest
from app.authorization.membership_policy import lock_organization
from app.core.errors import ApplicationError
from app.core.rate_limit import enforce_rate_limit
from app.identity.recovery_delivery import deliver_recovery_token
from app.platform.crypto import generate_token, hash_password, hash_token
from app.platform.database.models import (
    AccountRecoveryToken,
    Membership,
    Organization,
    PasswordCredential,
    User,
    UserSession,
)
from app.platform.database.session import get_db_session, set_organization_context, set_user_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event

router = APIRouter()
RECOVERY_TTL_MINUTES = 30


def lock_recovery_sessions(session: Session) -> None:
    """Serialize cross-organization session changes before any tenant or row lock."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('sentinelai-org-switch', 3))")
    )


def active_membership(session: Session, user_id: UUID) -> Membership | None:
    return session.scalar(
        select(Membership)
        .where(
            Membership.user_id == user_id,
            Membership.status == "active",
            Membership.deleted_at.is_(None),
        )
        .order_by(Membership.created_at, Membership.id)
    )


@router.post("/account-recovery", status_code=status.HTTP_202_ACCEPTED)
def request_account_recovery(
    payload: RecoveryRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> Response:
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(
        "account-recovery",
        f"{str(payload.email).lower()}:{client_ip}",
        limit=3,
        window_seconds=3600,
    )
    user = session.scalar(
        select(User).where(User.email == str(payload.email).lower(), User.deleted_at.is_(None))
    )
    if user is not None and user.status == "active":
        set_user_context(session, user.id)
        membership = active_membership(session, user.id)
        if membership is not None:
            set_organization_context(session, membership.organization_id)
            lock_organization(session, membership.organization_id)
            organization = session.get(Organization, membership.organization_id)
            if (
                organization is None
                or organization.deleted_at is not None
                or organization.status != "active"
            ):
                return Response(status_code=status.HTTP_202_ACCEPTED)
            raw_token = generate_token()
            recovery_id = uuid4()
            session.add(
                AccountRecoveryToken(
                    id=recovery_id,
                    user_id=user.id,
                    token_hash=hash_token(raw_token),
                    expires_at=datetime.now(UTC) + timedelta(minutes=RECOVERY_TTL_MINUTES),
                )
            )
            record_event(
                session,
                request_id=getattr(request.state, "request_id", "unavailable"),
                action="account_recovery.requested",
                resource_type="user",
                resource_id=user.id,
                outcome="accepted",
                organization_id=membership.organization_id,
                actor_type="recovery",
            )
            emit_event(
                session,
                organization_id=membership.organization_id,
                aggregate_type="user",
                aggregate_id=user.id,
                event_type="account_recovery.requested",
                idempotency_key=f"recovery.requested:{recovery_id}",
            )
            session.commit()
            deliver_recovery_token(str(user.email), raw_token)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/account-recovery/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_account_recovery(
    payload: RecoveryConfirmRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> Response:
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(
        "account-recovery-confirm",
        f"{hash_token(payload.token)}:{client_ip}",
        limit=5,
        window_seconds=3600,
    )
    token_statement = select(AccountRecoveryToken).where(
        AccountRecoveryToken.token_hash == hash_token(payload.token),
        AccountRecoveryToken.consumed_at.is_(None),
        AccountRecoveryToken.expires_at > datetime.now(UTC),
    )
    # Resolve only the lock scope first; all authorization is rechecked after acquiring it.
    token = session.scalar(token_statement)
    if token is None:
        raise ApplicationError("RECOVERY_TOKEN_INVALID", "El token no es válido o expiró.", 400)
    lock_recovery_sessions(session)
    set_user_context(session, token.user_id)
    membership = active_membership(session, token.user_id)
    if membership is None:
        raise ApplicationError("RECOVERY_TOKEN_INVALID", "El token no es válido o expiró.", 400)
    set_organization_context(session, membership.organization_id)
    lock_organization(session, membership.organization_id)
    # Lock order: cross-organization guard, organization, token row, sessions, audit.
    token = session.scalar(
        token_statement.where(AccountRecoveryToken.expires_at > datetime.now(UTC))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if token is None or token.expires_at <= datetime.now(UTC):
        raise ApplicationError("RECOVERY_TOKEN_INVALID", "El token no es válido o expiró.", 400)
    membership = active_membership(session, token.user_id)
    organization = session.get(Organization, membership.organization_id) if membership else None
    if (
        organization is None
        or organization.deleted_at is not None
        or organization.status != "active"
    ):
        raise ApplicationError("RECOVERY_TOKEN_INVALID", "El token no es válido o expiró.", 400)
    user = session.get(User, token.user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        raise ApplicationError("RECOVERY_TOKEN_INVALID", "El token no es válido o expiró.", 400)
    credential = session.get(PasswordCredential, token.user_id)
    if credential is None:
        session.add(
            PasswordCredential(
                user_id=token.user_id, password_hash=hash_password(payload.new_password)
            )
        )
    else:
        credential.password_hash = hash_password(payload.new_password)
        credential.changed_at = datetime.now(UTC)
    token.consumed_at = datetime.now(UTC)
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == token.user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="account_recovery.completed",
        resource_type="user",
        resource_id=token.user_id,
        outcome="success",
        organization_id=membership.organization_id,
        actor_type="recovery",
    )
    emit_event(
        session,
        organization_id=membership.organization_id,
        aggregate_type="user",
        aggregate_id=token.user_id,
        event_type="account_recovery.completed",
        idempotency_key=f"recovery.completed:{token.id}",
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
