from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_actor
from app.api.v1.schemas import ActiveOrganizationRequest
from app.api.v1.session import create_session, set_session_cookie
from app.authorization.membership_policy import lock_organization
from app.authorization.policy import ActorContext
from app.core.errors import ApplicationError
from app.platform.database.models import Membership, Organization, User, UserSession
from app.platform.database.session import get_db_session, set_organization_context, set_user_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event

router = APIRouter()


@router.get("/me")
def get_me(
    actor: ActorContext = Depends(get_actor), session: Session = Depends(get_db_session)
) -> dict[str, object]:
    set_organization_context(session, actor.organization_id)
    user = session.get(User, actor.user_id)
    if user is None:
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "active_organization_id": str(actor.organization_id),
        "roles": [actor.role_code],
        "permissions": sorted(actor.permissions),
    }


@router.post("/me/active-organization")
def set_active_organization(
    payload: ActiveOrganizationRequest,
    response: Response,
    request: Request,
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    set_user_context(session, actor.user_id)
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == actor.user_id,
            Membership.organization_id == payload.organization_id,
            Membership.status == "active",
            Membership.deleted_at.is_(None),
        )
    )
    if membership is None:
        raise ApplicationError(
            "ORGANIZATION_NOT_FOUND", "El recurso solicitado no está disponible.", 404
        )
    lock_organization(session, payload.organization_id)
    set_organization_context(session, payload.organization_id)
    organization = session.get(Organization, payload.organization_id)
    if (
        organization is None
        or organization.deleted_at is not None
        or organization.status != "active"
    ):
        raise ApplicationError(
            "ORGANIZATION_NOT_FOUND", "El recurso solicitado no está disponible.", 404
        )
    session.execute(
        update(UserSession)
        .where(UserSession.id == actor.session_id)
        .values(revoked_at=datetime.now(UTC))
    )
    token, csrf_token, _ = create_session(
        session, actor.user_id, payload.organization_id, membership.id
    )
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="organization.activate",
        resource_type="organization",
        outcome="success",
        organization_id=payload.organization_id,
        actor_user_id=actor.user_id,
    )
    emit_event(
        session,
        organization_id=payload.organization_id,
        aggregate_type="session",
        aggregate_id=actor.session_id,
        event_type="organization.activated",
        idempotency_key=f"organization.activated:{actor.session_id}",
    )
    session.commit()
    set_session_cookie(response, token)
    return {"active_organization_id": str(payload.organization_id), "csrf_token": csrf_token}
