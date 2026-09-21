from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_actor
from app.api.v1.organizations import current_organization
from app.api.v1.schemas import MembershipCreateRequest, MembershipPatchRequest
from app.authorization.membership_policy import (
    assert_membership_change_allowed,
    lock_organization,
    protect_last_owner,
    resolve_assignable_role,
)
from app.authorization.policy import ActorContext
from app.core.errors import ApplicationError
from app.core.rate_limit import enforce_rate_limit
from app.platform.database.models import Membership, Role, User, UserSession
from app.platform.database.session import get_db_session, set_organization_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event

router = APIRouter()


def membership_payload(membership: Membership, role: Role) -> dict[str, object]:
    return {
        "id": str(membership.id),
        "user_id": str(membership.user_id),
        "organization_id": str(membership.organization_id),
        "role_code": role.code,
        "status": membership.status,
        "version": membership.version,
        "created_at": membership.created_at.isoformat(),
        "updated_at": membership.updated_at.isoformat(),
    }


def require_current_organization(
    session: Session, actor: ActorContext, organization_id: UUID
) -> None:
    current_organization(session, actor, organization_id)
    set_organization_context(session, organization_id)


def authorize_change(
    session: Session,
    actor: ActorContext,
    request: Request,
    *,
    action: str,
    organization_id: UUID,
    target_user_id: UUID,
    membership_id: UUID | None = None,
    current_role_code: str | None = None,
    new_role_code: str | None = None,
) -> None:
    """Persist safe authorization denials in a clean transaction, then reject.

    This helper runs before mutating the target. Rollback also discards ancillary
    session updates; no partially modified aggregate can accompany a denial.
    Version conflicts are handled separately and never create audit/outbox rows.
    """
    try:
        assert_membership_change_allowed(
            actor,
            action=action,
            organization_id=organization_id,
            target_user_id=target_user_id,
            current_role_code=current_role_code,
            new_role_code=new_role_code,
        )
    except ApplicationError as error:
        if error.status_code == 403:
            session.rollback()
            set_organization_context(session, actor.organization_id)
            record_event(
                session,
                request_id=getattr(request.state, "request_id", "unavailable"),
                action="membership.change_denied",
                resource_type="membership",
                resource_id=membership_id,
                outcome="denied",
                organization_id=actor.organization_id,
                actor_user_id=actor.user_id,
                details={"operation": action, "reason": "authorization_policy"},
            )
            session.commit()
        raise


def locked_membership(
    session: Session, organization_id: UUID, membership_id: UUID, expected_version: int
) -> Membership:
    lock_organization(session, organization_id)
    membership = session.scalar(
        select(Membership)
        .where(
            Membership.id == membership_id,
            Membership.organization_id == organization_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if membership is None:
        raise ApplicationError(
            "MEMBERSHIP_NOT_FOUND", "El recurso solicitado no está disponible.", 404
        )
    if membership.version != expected_version:
        raise ApplicationError(
            "VERSION_CONFLICT", "El recurso cambió; actualiza e inténtalo de nuevo.", 409
        )
    if membership.deleted_at is not None:
        raise ApplicationError(
            "MEMBERSHIP_NOT_FOUND", "El recurso solicitado no está disponible.", 404
        )
    return membership


@router.get("/organizations/{organization_id}/memberships")
def list_memberships(
    organization_id: UUID,
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    actor.require("membership:read")
    require_current_organization(session, actor, organization_id)
    memberships = session.scalars(
        select(Membership)
        .where(Membership.deleted_at.is_(None))
        .order_by(Membership.created_at, Membership.id)
        .limit(100)
    ).all()
    return {
        "items": [
            membership_payload(item, session.get(Role, item.role_id)) for item in memberships
        ],
        "next_cursor": None,
    }


@router.post("/organizations/{organization_id}/memberships", status_code=status.HTTP_201_CREATED)
def create_membership(
    organization_id: UUID,
    payload: MembershipCreateRequest,
    request: Request,
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    enforce_rate_limit(
        "membership-admin",
        f"{actor.user_id}:{organization_id}",
        limit=20,
        window_seconds=3600,
    )
    require_current_organization(session, actor, organization_id)
    lock_organization(session, organization_id)
    user = session.scalar(
        select(User).where(User.email == str(payload.email).lower(), User.deleted_at.is_(None))
    )
    # Role authorization precedes the unknown-user response, while using the actual
    # target identity whenever one exists. No target details enter denial events.
    authorize_change(
        session,
        actor,
        request,
        action="create",
        organization_id=organization_id,
        target_user_id=user.id if user else UUID(int=0),
        new_role_code=payload.role_code,
    )
    role = resolve_assignable_role(session, actor, organization_id, payload.role_code)
    if user is None:
        raise ApplicationError("USER_NOT_FOUND", "No se puede crear la membresía solicitada.", 422)
    existing = session.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user.id,
            Membership.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise ApplicationError("MEMBERSHIP_EXISTS", "La membresía ya existe.", 409)
    membership = Membership(organization_id=organization_id, user_id=user.id, role_id=role.id)
    session.add(membership)
    session.flush()
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="membership.invited",
        resource_type="membership",
        resource_id=membership.id,
        outcome="success",
        organization_id=organization_id,
        actor_user_id=actor.user_id,
    )
    emit_event(
        session,
        organization_id=organization_id,
        aggregate_type="membership",
        aggregate_id=membership.id,
        event_type="membership.invited",
        idempotency_key=f"membership.created:{membership.id}:{membership.version}",
    )
    session.commit()
    return membership_payload(membership, role)


@router.patch("/organizations/{organization_id}/memberships/{membership_id}")
def update_membership(
    organization_id: UUID,
    membership_id: UUID,
    payload: MembershipPatchRequest,
    request: Request,
    if_match: int = Header(alias="If-Match"),
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    enforce_rate_limit(
        "membership-admin",
        f"{actor.user_id}:{organization_id}",
        limit=20,
        window_seconds=3600,
    )
    require_current_organization(session, actor, organization_id)
    if payload.role_code is None and payload.status is None:
        raise ApplicationError("VALIDATION_ERROR", "Debe indicarse al menos un cambio.", 422)
    membership = locked_membership(session, organization_id, membership_id, if_match)
    current_role = session.get(Role, membership.role_id)
    authorize_change(
        session,
        actor,
        request,
        action="update",
        organization_id=organization_id,
        target_user_id=membership.user_id,
        membership_id=membership.id,
        current_role_code=current_role.code if current_role else None,
        new_role_code=payload.role_code,
    )
    role = (
        resolve_assignable_role(session, actor, organization_id, payload.role_code)
        if payload.role_code
        else current_role
    )
    protect_last_owner(session, membership, current_role, role, payload.status or membership.status)
    membership.role_id = role.id
    if payload.status:
        membership.status = payload.status
        if payload.status == "suspended":
            session.execute(
                update(UserSession)
                .where(
                    UserSession.user_id == membership.user_id,
                    UserSession.organization_id == organization_id,
                    UserSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
    membership.version += 1
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="membership.updated",
        resource_type="membership",
        resource_id=membership.id,
        outcome="success",
        organization_id=organization_id,
        actor_user_id=actor.user_id,
    )
    emit_event(
        session,
        organization_id=organization_id,
        aggregate_type="membership",
        aggregate_id=membership.id,
        event_type="membership.updated",
        idempotency_key=f"membership.updated:{membership.id}:{membership.version}",
    )
    session.commit()
    return membership_payload(membership, role)


@router.delete(
    "/organizations/{organization_id}/memberships/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_membership(
    organization_id: UUID,
    membership_id: UUID,
    request: Request,
    if_match: int = Header(alias="If-Match"),
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> Response:
    enforce_rate_limit(
        "membership-admin",
        f"{actor.user_id}:{organization_id}",
        limit=20,
        window_seconds=3600,
    )
    require_current_organization(session, actor, organization_id)
    membership = locked_membership(session, organization_id, membership_id, if_match)
    current_role = session.get(Role, membership.role_id)
    authorize_change(
        session,
        actor,
        request,
        action="revoke",
        organization_id=organization_id,
        target_user_id=membership.user_id,
        membership_id=membership.id,
        current_role_code=current_role.code if current_role else None,
    )
    protect_last_owner(session, membership, current_role, current_role, "suspended")
    membership.deleted_at = datetime.now(UTC)
    membership.version += 1
    session.execute(
        update(UserSession)
        .where(
            UserSession.user_id == membership.user_id,
            UserSession.organization_id == organization_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="membership.revoked",
        resource_type="membership",
        resource_id=membership.id,
        outcome="success",
        organization_id=organization_id,
        actor_user_id=actor.user_id,
    )
    emit_event(
        session,
        organization_id=organization_id,
        aggregate_type="membership",
        aggregate_id=membership.id,
        event_type="membership.revoked",
        idempotency_key=f"membership.revoked:{membership.id}:{membership.version}",
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
