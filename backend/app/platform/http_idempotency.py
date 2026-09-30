"""HTTP replay for asset POST/DELETE, distinct from internal outbox keys.

The caller holds the existing organization lock until commit, after fresh actor
and permission validation. Failed requests never reserve keys. Expired records
are recycled only by the identical actor/tenant/method/resource/key context.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.policy import ActorContext
from app.core.errors import ApplicationError
from app.platform.database.models import HttpIdempotencyRecord


class HttpReplay:
    def __init__(
        self,
        session: Session,
        actor: ActorContext,
        *,
        key: str,
        method: str,
        route: str,
        payload: dict[str, object],
    ):
        self.session = session
        self.context = {
            "organization_id": actor.organization_id,
            "actor_id": actor.user_id,
            "method": method,
            "route": route,
            "key_hash": hashlib.sha256(key.encode()).hexdigest(),
        }
        self.fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        self.record = session.scalar(select(HttpIdempotencyRecord).filter_by(**self.context))

    def replay(self) -> dict[str, object] | None:
        if self.record is None or self.record.expires_at <= datetime.now(UTC):
            return None
        if self.record.fingerprint != self.fingerprint:
            raise ApplicationError("IDEMPOTENCY_CONFLICT", "La clave tiene otro contenido.", 409)
        return self.record.response_body

    def save(self, *, asset_id: UUID, status_code: int, body: dict[str, object]) -> None:
        now = datetime.now(UTC)
        values = {
            "fingerprint": self.fingerprint,
            "asset_id": asset_id,
            "status_code": status_code,
            "response_body": body,
            "created_at": now,
            "expires_at": now + timedelta(hours=24),
        }
        if self.record is None:
            self.session.add(HttpIdempotencyRecord(**self.context, **values))
        else:
            for key, value in values.items():
                setattr(self.record, key, value)
        self.session.flush()
