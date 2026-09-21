from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.dependencies import SESSION_COOKIE, get_actor
from app.api.v1.schemas import LoginRequest
from app.authorization.membership_policy import lock_organization
from app.authorization.policy import ActorContext
from app.core.config import get_settings
from app.core.errors import ApplicationError
from app.core.rate_limit import enforce_rate_limit
from app.platform.crypto import generate_token, hash_password, hash_token, verify_password
from app.platform.database.models import (
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
# Unknown accounts still perform an Argon2 verification. This is not a credential
# and is never persisted; it avoids the obvious fast-path account timing oracle.
_DUMMY_PASSWORD_HASH = hash_password(generate_token())


def create_session(
    session: Session, user_id: UUID, organization_id: UUID, membership_id: UUID
) -> tuple[str, str, UUID]:
    settings = get_settings()
    now = datetime.now(UTC)
    token = generate_token()
    csrf_token = generate_token()
    session_id = uuid4()
    session.add(
        UserSession(
            id=session_id,
            user_id=user_id,
            organization_id=organization_id,
            membership_id=membership_id,
            token_hash=hash_token(token),
            csrf_secret_hash=hash_token(csrf_token),
            expires_at=now + timedelta(minutes=settings.session_absolute_minutes),
            idle_expires_at=now + timedelta(minutes=settings.session_idle_minutes),
        )
    )
    return token, csrf_token, session_id


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=get_settings().session_absolute_minutes * 60,
    )


@router.post("/session", status_code=status.HTTP_200_OK)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(
        "login",
        f"{str(payload.email).lower()}:{client_ip}",
        limit=5,
        window_seconds=60,
    )
    user = session.scalar(
        select(User).where(User.email == str(payload.email).lower(), User.deleted_at.is_(None))
    )
    credential = session.get(PasswordCredential, user.id) if user else None
    valid, replacement = verify_password(
        credential.password_hash if credential else _DUMMY_PASSWORD_HASH, payload.password
    )
    if user is not None:
        set_user_context(session, user.id)
    membership = (
        session.scalar(
            select(Membership)
            .where(
                Membership.user_id == user.id,
                Membership.status == "active",
                Membership.deleted_at.is_(None),
            )
            .order_by(Membership.created_at, Membership.id)
        )
        if user
        else None
    )
    if not valid or user is None or user.status != "active" or membership is None:
        if user is not None and membership is not None:
            set_organization_context(session, membership.organization_id)
            record_event(
                session,
                request_id=getattr(request.state, "request_id", "unavailable"),
                action="session.login",
                resource_type="session",
                outcome="denied",
                organization_id=membership.organization_id,
                actor_type="anonymous",
            )
            session.commit()
        raise ApplicationError("INVALID_CREDENTIALS", "Credenciales no válidas.", 401)
    verified_hash = credential.password_hash
    login_user_id = user.id
    login_membership_id = membership.id
    login_organization_id = membership.organization_id
    set_organization_context(session, membership.organization_id)
    lock_organization(session, membership.organization_id)
    # The lock may have waited behind suspension or recovery. Refresh identity-map
    # snapshots before issuing a session, and never reuse an outdated password proof.
    user = session.scalar(
        select(User)
        .where(User.id == login_user_id, User.status == "active", User.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    membership = session.scalar(
        select(Membership)
        .where(
            Membership.id == login_membership_id,
            Membership.user_id == login_user_id,
            Membership.organization_id == login_organization_id,
            Membership.status == "active",
            Membership.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    credential = session.get(PasswordCredential, login_user_id, populate_existing=True)
    if user is None or membership is None or credential is None:
        raise ApplicationError("INVALID_CREDENTIALS", "Credenciales no válidas.", 401)
    if credential.password_hash != verified_hash:
        valid, replacement = verify_password(credential.password_hash, payload.password)
        if not valid:
            raise ApplicationError("INVALID_CREDENTIALS", "Credenciales no válidas.", 401)
    organization = session.get(Organization, membership.organization_id)
    if (
        organization is None
        or organization.deleted_at is not None
        or organization.status != "active"
    ):
        raise ApplicationError("INVALID_CREDENTIALS", "Credenciales no válidas.", 401)
    if replacement:
        credential.password_hash = replacement
    token, csrf_token, session_id = create_session(
        session, user.id, membership.organization_id, membership.id
    )
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="session.login",
        resource_type="session",
        outcome="success",
        organization_id=membership.organization_id,
        actor_user_id=user.id,
    )
    emit_event(
        session,
        organization_id=membership.organization_id,
        aggregate_type="session",
        aggregate_id=session_id,
        event_type="session.login",
        idempotency_key=f"session.login:{session_id}",
    )
    session.commit()
    set_session_cookie(response, token)
    return {"csrf_token": csrf_token, "expires_in": get_settings().session_absolute_minutes * 60}


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    request: Request,
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> Response:
    session.execute(
        update(UserSession)
        .where(UserSession.id == actor.session_id)
        .values(revoked_at=datetime.now(UTC))
    )
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="session.logout",
        resource_type="session",
        outcome="success",
        organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )
    emit_event(
        session,
        organization_id=actor.organization_id,
        aggregate_type="session",
        aggregate_id=actor.session_id,
        event_type="session.logout",
        idempotency_key=f"session.logout:{actor.session_id}",
    )
    session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/session/rotate")
def rotate_session(
    response: Response,
    request: Request,
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    enforce_rate_limit("session-rotate", str(actor.session_id), limit=10, window_seconds=3600)
    session.execute(
        update(UserSession)
        .where(UserSession.id == actor.session_id)
        .values(revoked_at=datetime.now(UTC))
    )
    token, csrf_token, session_id = create_session(
        session, actor.user_id, actor.organization_id, actor.membership_id
    )
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="session.rotate",
        resource_type="session",
        outcome="success",
        organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )
    emit_event(
        session,
        organization_id=actor.organization_id,
        aggregate_type="session",
        aggregate_id=session_id,
        event_type="session.rotated",
        idempotency_key=f"session.rotated:{actor.session_id}",
    )
    session.commit()
    set_session_cookie(response, token)
    return {"csrf_token": csrf_token}
