"""Append-only audit chain with per-organization transactional sequencing."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.platform.database.models import SecurityAuditEvent


def _material(event: SecurityAuditEvent) -> bytes:
    if event.hash_version == 1:
        # Original Sprint 1B hashes covered this smaller, explicitly legacy contract.
        document = {
            "action": event.action,
            "outcome": event.outcome,
            "previous": event.prev_hash,
            "request_id": event.request_id,
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if event.hash_version not in (2, 3):
        raise ValueError("Unsupported audit hash version.")
    document = {
        "action": event.action,
        "actor_type": event.actor_type,
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "details": event.details,
        "event_id": str(event.id),
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "organization_id": str(event.organization_id),
        "outcome": event.outcome,
        "previous_hash": event.prev_hash,
        "request_id": event.request_id,
        "resource_id": str(event.resource_id) if event.resource_id else None,
        "resource_type": event.resource_type,
    }
    if event.hash_version == 3:
        document["sequence"] = event.sequence
        document["hash_version"] = event.hash_version
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def record_event(
    session: Session,
    *,
    request_id: str,
    action: str,
    resource_type: str,
    outcome: str,
    organization_id: UUID,
    actor_user_id: UUID | None = None,
    actor_type: str = "user",
    resource_id: UUID | None = None,
    details: dict[str, object] | None = None,
) -> SecurityAuditEvent:
    """Append one chained event in the caller's existing transaction."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:organization_id, 0))"),
        {"organization_id": str(organization_id)},
    )
    # Session autoflush is disabled. Make earlier appends visible before selecting the tail.
    session.flush()
    previous = session.scalar(
        select(SecurityAuditEvent)
        .where(SecurityAuditEvent.organization_id == organization_id)
        .order_by(SecurityAuditEvent.sequence.desc())
        .limit(1)
    )
    event = SecurityAuditEvent(
        id=uuid4(),
        organization_id=organization_id,
        sequence=previous.sequence + 1 if previous else 1,
        hash_version=3,
        occurred_at=datetime.now(UTC),
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        request_id=request_id,
        details=details or {},
        prev_hash=previous.event_hash if previous else None,
        event_hash="",
    )
    event.event_hash = hashlib.sha256(_material(event)).hexdigest()
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session, organization_id: UUID) -> tuple[bool, str | None]:
    if session.scalar(text("SELECT current_setting('app.organization_id', true)")) != str(
        organization_id
    ):
        raise ValueError("Audit verification requires the matching organization context.")
    previous_hash: str | None = None
    events = session.scalars(
        select(SecurityAuditEvent)
        .where(SecurityAuditEvent.organization_id == organization_id)
        .order_by(SecurityAuditEvent.sequence)
    ).all()
    for expected_sequence, event in enumerate(events, start=1):
        if (
            event.sequence != expected_sequence
            or event.hash_version not in (1, 2, 3)
            or event.prev_hash != previous_hash
            or hashlib.sha256(_material(event)).hexdigest() != event.event_hash
        ):
            return False, str(event.id)
        previous_hash = event.event_hash
    return True, None
