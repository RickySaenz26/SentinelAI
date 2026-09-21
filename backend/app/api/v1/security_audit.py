from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_actor
from app.authorization.policy import ActorContext
from app.platform.database.models import SecurityAuditEvent
from app.platform.database.session import get_db_session, set_organization_context

router = APIRouter()


@router.get("/security-audit-events")
def list_security_audit_events(
    limit: int = Query(default=50, ge=1, le=100),
    action: str | None = Query(default=None, min_length=1, max_length=128),
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    actor.require("security_audit:read")
    set_organization_context(session, actor.organization_id)
    statement = select(SecurityAuditEvent).order_by(SecurityAuditEvent.sequence.desc())
    if action:
        statement = statement.where(SecurityAuditEvent.action == action)
    events = session.scalars(statement.limit(limit)).all()
    return {
        "items": [
            {
                "id": str(event.id),
                "occurred_at": event.occurred_at.isoformat(),
                "sequence": event.sequence,
                "hash_version": event.hash_version,
                "actor_type": event.actor_type,
                "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "action": event.action,
                "resource_type": event.resource_type,
                "resource_id": str(event.resource_id) if event.resource_id else None,
                "outcome": event.outcome,
                "request_id": event.request_id,
                "details": event.details,
                "prev_hash": event.prev_hash,
                "event_hash": event.event_hash,
            }
            for event in events
        ],
        "next_cursor": None,
    }
