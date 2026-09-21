from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_actor
from app.api.v1.schemas import OrganizationPatchRequest
from app.authorization.membership_policy import lock_organization
from app.authorization.policy import ActorContext
from app.core.errors import ApplicationError
from app.platform.database.models import Organization
from app.platform.database.session import get_db_session, set_organization_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event

router = APIRouter()


def current_organization(
    session: Session, actor: ActorContext, organization_id: UUID, *, for_update: bool = False
) -> Organization:
    if organization_id != actor.organization_id:
        raise ApplicationError(
            "ORGANIZATION_NOT_FOUND", "El recurso solicitado no está disponible.", 404
        )
    set_organization_context(session, actor.organization_id)
    statement = select(Organization).where(Organization.id == organization_id)
    if for_update:
        lock_organization(session, organization_id)
        statement = statement.with_for_update().execution_options(populate_existing=True)
    organization = session.scalar(statement)
    if (
        organization is None
        or organization.deleted_at is not None
        or organization.status != "active"
    ):
        raise ApplicationError(
            "ORGANIZATION_NOT_FOUND", "El recurso solicitado no está disponible.", 404
        )
    return organization


@router.get("/organizations")
def list_organizations(
    actor: ActorContext = Depends(get_actor), session: Session = Depends(get_db_session)
) -> dict[str, object]:
    actor.require("organization:read")
    set_organization_context(session, actor.organization_id)
    organizations = session.scalars(
        select(Organization).where(Organization.deleted_at.is_(None))
    ).all()
    return {
        "items": [
            {"id": str(org.id), "name": org.name, "slug": org.slug, "version": org.version}
            for org in organizations
        ]
    }


@router.get("/organizations/{organization_id}")
def get_organization(
    organization_id: UUID,
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    actor.require("organization:read")
    org = current_organization(session, actor, organization_id)
    return {"id": str(org.id), "name": org.name, "slug": org.slug, "version": org.version}


@router.patch("/organizations/{organization_id}")
def update_organization(
    organization_id: UUID,
    payload: OrganizationPatchRequest,
    request: Request,
    if_match: int = Header(alias="If-Match"),
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    actor.require("organization:update")
    org = current_organization(session, actor, organization_id, for_update=True)
    if org.version != if_match:
        raise ApplicationError(
            "VERSION_CONFLICT", "El recurso cambió; actualiza e inténtalo de nuevo.", 409
        )
    org.name = payload.name
    org.version += 1
    record_event(
        session,
        request_id=getattr(request.state, "request_id", "unavailable"),
        action="organization.updated",
        resource_type="organization",
        resource_id=org.id,
        outcome="success",
        organization_id=org.id,
        actor_user_id=actor.user_id,
    )
    emit_event(
        session,
        organization_id=org.id,
        aggregate_type="organization",
        aggregate_id=org.id,
        event_type="organization.updated",
        idempotency_key=f"organization.updated:{org.id}:{org.version}",
    )
    session.commit()
    return {"id": str(org.id), "name": org.name, "slug": org.slug, "version": org.version}
