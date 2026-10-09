"""Inventory use cases; no scanner, DNS, verification or eligibility claims."""

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.assets.authority import require_admission, require_target
from app.assets.repository import AssetRepository
from app.assets.schemas import AssetArchive, AssetCreate, AssetPatch, AssetResponse
from app.authorization.membership_policy import lock_organization
from app.authorization.policy import ActorContext
from app.core.errors import ApplicationError
from app.core.rate_limit import enforce_rate_limit
from app.platform.database.session import set_organization_context
from app.platform.http_idempotency import HttpReplay
from app.platform.outbox import emit_event
from app.security_audit.service import record_event


class AssetService:
    def __init__(self, session: Session, actor: ActorContext, request_id: str):
        self.session, self.actor, self.request_id = session, actor, request_id
        self.repository = AssetRepository(session, actor.organization_id)

    def authorize(self, action: str) -> None:
        self.actor.require(f"asset:{action}")
        lock_organization(self.session, self.actor.organization_id)
        set_organization_context(self.session, self.actor.organization_id)
        limit = {"read": 90, "create": 30, "update": 30, "archive": 20}[action]
        enforce_rate_limit(
            f"asset:{action}",
            f"{self.actor.organization_id}:{self.actor.user_id}",
            limit=limit,
            window_seconds=60,
        )

    def document(self, asset) -> dict[str, object]:
        return AssetResponse(
            id=asset.id,
            type=asset.type,
            target=asset.canonical_target,
            display_name=asset.display_name,
            criticality=asset.criticality,
            ownership_status=asset.ownership_status,
            version=asset.version,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
            archived_at=asset.deleted_at,
            archive_reason=asset.archive_reason,
            request_id=self.request_id,
        ).model_dump(mode="json")

    def find(self, asset_id: UUID, *, lock: bool = False):
        asset = self.repository.get(asset_id, lock=lock)
        if asset is None:
            raise ApplicationError("ASSET_NOT_FOUND", "El recurso no está disponible.", 404)
        return asset

    def event(self, asset, action: str) -> None:
        record_event(
            self.session,
            request_id=self.request_id,
            organization_id=self.actor.organization_id,
            actor_user_id=self.actor.user_id,
            action=f"asset.{action}",
            resource_type="asset",
            resource_id=asset.id,
            outcome="success",
            details={"version": asset.version, "policy_hash": asset.policy_hash},
        )
        emit_event(
            self.session,
            organization_id=self.actor.organization_id,
            aggregate_type="asset",
            aggregate_id=asset.id,
            event_type=f"asset.{action}",
            idempotency_key=f"asset.{action}:{asset.id}:{asset.version}",
            payload={"version": asset.version},
        )

    def create(self, payload: AssetCreate, key: str):
        self.authorize("create")
        # Policy is checked even before replay: platform:admin never bypasses it.
        policy = require_target(self.session, self.actor.organization_id, payload.target).policy
        replay = HttpReplay(
            self.session,
            self.actor,
            key=key,
            method="POST",
            route="/api/v1/assets",
            payload=payload.model_dump(),
        )
        body = replay.replay()
        if body is not None:
            self.find(UUID(str(body["id"])))
            require_admission(self.session, self.actor.organization_id, UUID(str(body["id"])))
            return body, True
        if self.repository.target_exists(payload.target):
            raise ApplicationError("ASSET_ALREADY_EXISTS", "El activo ya está registrado.", 409)
        if self.repository.active_count() >= policy.max_active_assets_per_tenant:
            raise ApplicationError("ASSET_LIMIT_REACHED", "Límite de activos alcanzado.", 409)
        asset = self.repository.create(
            target=payload.target,
            display_name=payload.display_name,
            criticality=payload.criticality,
            policy_hash=policy.fingerprint,
        )
        body = self.document(asset)
        self.event(asset, "created")
        replay.save(asset_id=asset.id, status_code=201, body=body)
        self.session.commit()
        return body, False

    def get(self, asset_id: UUID):
        self.authorize("read")
        return self.document(self.find(asset_id))

    def list(self, *, limit: int, cursor: str | None, status: str, criticality, query):
        self.authorize("read")
        after = None
        if cursor:
            try:
                values = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if (
                    not isinstance(values, list)
                    or len(values) != 3
                    or not all(isinstance(value, str) for value in values)
                ):
                    raise ValueError("Invalid cursor structure")
                org, stamp, identifier = values
                moment = datetime.fromisoformat(stamp)
                if org != str(self.actor.organization_id) or moment.tzinfo is None:
                    raise ValueError("Invalid cursor context")
                after = moment, UUID(identifier)
            except (ValueError, TypeError, binascii.Error) as exc:
                raise ApplicationError("INVALID_CURSOR", "Cursor no válido.", 422) from exc
        rows = self.repository.page(
            limit=limit,
            after=after,
            status=status,
            criticality=criticality,
            query=query,
        )
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    [str(self.actor.organization_id), last.created_at.isoformat(), str(last.id)]
                ).encode()
            ).decode()
        return {
            "items": [self.document(row) for row in rows[:limit]],
            "page": {"next_cursor": next_cursor, "limit": limit},
            "request_id": self.request_id,
        }

    @staticmethod
    def check_version(asset, version: int) -> None:
        if asset.version != version:
            raise ApplicationError(
                "VERSION_CONFLICT", "El recurso cambió; actualiza e inténtalo de nuevo.", 409
            )
        if asset.deleted_at is not None:
            raise ApplicationError("ASSET_ARCHIVED", "El activo está archivado.", 409)

    def update(self, asset_id: UUID, payload: AssetPatch, version: int):
        self.authorize("update")
        asset = self.find(asset_id, lock=True)
        self.check_version(asset, version)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(asset, field, value)
        asset.version += 1
        asset.updated_at = datetime.now(UTC)
        self.event(asset, "updated")
        self.session.commit()
        return self.document(asset)

    def archive(self, asset_id: UUID, payload: AssetArchive, version: int, key: str) -> bool:
        self.authorize("archive")
        asset = self.find(asset_id, lock=True)
        replay = HttpReplay(
            self.session,
            self.actor,
            key=key,
            method="DELETE",
            route=f"/api/v1/assets/{asset_id}",
            payload={**payload.model_dump(), "if_match": version},
        )
        if replay.replay() is not None:
            return True
        self.check_version(asset, version)
        asset.deleted_at = asset.updated_at = datetime.now(UTC)
        asset.archive_reason = payload.reason
        asset.version += 1
        self.event(asset, "archived")
        replay.save(asset_id=asset.id, status_code=204, body={})
        self.session.commit()
        return False
