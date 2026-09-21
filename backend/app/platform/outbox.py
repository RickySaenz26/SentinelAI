"""Transactional outbox boundary for future asynchronous publishers."""

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ApplicationError
from app.platform.database.models import OutboxEvent


def emit_event(
    session: Session,
    *,
    organization_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, object] | None = None,
) -> OutboxEvent:
    """Deduplicate an internal mutation key inside the caller's transaction.

    Keys identify a stable mutation (aggregate version or session/token UUID), not a
    request or credential. They are never taken from an HTTP Idempotency-Key header.
    """
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", idempotency_key) is None:
        raise ValueError("Outbox idempotency key must contain 1-128 safe ASCII characters.")
    content = payload or {}
    session.flush()
    event = session.scalar(
        insert(OutboxEvent)
        .values(
            organization_id=organization_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=content,
            idempotency_key=idempotency_key,
        )
        .on_conflict_do_nothing(
            index_elements=[OutboxEvent.organization_id, OutboxEvent.idempotency_key],
            index_where=OutboxEvent.idempotency_key.is_not(None),
        )
        .returning(OutboxEvent)
    )
    if event is None:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.organization_id == organization_id,
                OutboxEvent.idempotency_key == idempotency_key,
            )
        )
        if event is None or (
            event.aggregate_type != aggregate_type
            or event.aggregate_id != aggregate_id
            or event.event_type != event_type
            or event.payload != content
        ):
            raise ApplicationError(
                "IDEMPOTENCY_CONFLICT", "La clave de evento ya tiene otro contenido.", 409
            )
    return event
