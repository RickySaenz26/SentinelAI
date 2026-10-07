"""HTTP use cases: fresh authority around I/O, no DB locks during storage access."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.evidence.service import EvidenceService, fail, receipt_from
from app.platform.database.models import Asset
from app.security_audit.service import record_event


def identity(actor):
    return actor.user_id, actor.organization_id, actor.membership_id, actor.session_id


class EvidenceAccess:
    def __init__(self, sessions, credentials, request_id, storage_factory):
        self.sessions, self.credentials = sessions, credentials
        self.request_id, self.storage_factory = request_id, storage_factory

    def authorize(self, session, asset_id, action):
        actor = self.credentials.authenticate(session)
        if action != "summary" and (
            actor.role_code in {"platform_admin", "viewer"} or "platform:admin" in actor.permissions
        ):
            fail("FORBIDDEN", 403)
        if action == "write" and actor.role_code not in {
            "org_owner",
            "security_manager",
            "analyst",
        }:
            fail("FORBIDDEN", 403)
        own = action in {"metadata", "read"} and (
            actor.role_code == "analyst" or f"evidence:{action}" not in actor.permissions
        )
        required = "evidence:read_own" if own else f"evidence:{action}"
        if required not in actor.permissions:
            fail("FORBIDDEN", 403)
        asset = session.scalar(
            select(Asset).where(
                Asset.organization_id == actor.organization_id, Asset.id == asset_id
            )
        )
        if asset is None:
            fail("ASSET_NOT_FOUND", 404)
        return actor, own

    def preflight(self, asset_id):
        with self.sessions.begin() as session:
            actor, _ = self.authorize(session, asset_id, "write")
            return identity(actor)

    def row(self, session, actor, own, asset_id, evidence_id):
        row = (
            session.execute(
                text(
                    "SELECT o.*,v.created_at AS submitted_at FROM evidence_versions v "
                    "JOIN evidence_operations o ON o.organization_id=v.organization_id "
                    "AND o.id=v.operation_id AND o.asset_id=v.asset_id AND o.version=v.version "
                    "WHERE v.organization_id=:org AND v.asset_id=:asset AND v.operation_id=:id "
                    "AND (NOT :own OR o.actor_id=:actor)"
                ),
                {
                    "org": actor.organization_id,
                    "asset": asset_id,
                    "id": evidence_id,
                    "own": own,
                    "actor": actor.user_id,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            fail("EVIDENCE_NOT_FOUND", 404)
        return dict(row)

    def metadata(self, row):
        until = row["submitted_at"] + timedelta(days=90)
        return {
            "id": row["id"],
            "asset_id": row["asset_id"],
            "version": row["version"],
            "submitted_at": row["submitted_at"],
            "retention_until": until,
            "content_available": datetime.now(UTC) < until,
            "request_id": self.request_id,
        }

    def summary(self, asset_id):
        with self.sessions.begin() as session:
            actor, _ = self.authorize(session, asset_id, "summary")
            count = session.scalar(
                text(
                    "SELECT count(*) FROM evidence_versions "
                    "WHERE organization_id=:org AND asset_id=:asset"
                ),
                {"org": actor.organization_id, "asset": asset_id},
            )
            return {
                "asset_id": asset_id,
                "evidence_count": count,
                "ownership_status": "unverified",
                "request_id": self.request_id,
            }

    def listing(self, asset_id, limit, after_version):
        with self.sessions.begin() as session:
            actor, own = self.authorize(session, asset_id, "metadata")
            rows = (
                session.execute(
                    text(
                        "SELECT o.id,o.asset_id,o.version,v.created_at AS submitted_at "
                        "FROM evidence_versions v JOIN evidence_operations o "
                        "ON o.organization_id=v.organization_id AND o.id=v.operation_id "
                        "WHERE v.organization_id=:org AND v.asset_id=:asset AND v.version>:after "
                        "AND (NOT :own OR o.actor_id=:actor) ORDER BY v.version LIMIT :limit"
                    ),
                    {
                        "org": actor.organization_id,
                        "asset": asset_id,
                        "own": own,
                        "actor": actor.user_id,
                        "after": after_version,
                        "limit": limit + 1,
                    },
                )
                .mappings()
                .all()
            )
            return {
                "items": [self.metadata(row) for row in rows[:limit]],
                "next_version": rows[limit - 1]["version"] if len(rows) > limit else None,
                "request_id": self.request_id,
            }

    def get(self, asset_id, evidence_id):
        with self.sessions.begin() as session:
            actor, own = self.authorize(session, asset_id, "metadata")
            return self.metadata(self.row(session, actor, own, asset_id, evidence_id))

    def content(self, asset_id, evidence_id):
        with self.sessions.begin() as session:
            actor, own = self.authorize(session, asset_id, "read")
            row = self.row(session, actor, own, asset_id, evidence_id)
            before = identity(actor)
            if not self.metadata(row)["content_available"]:
                fail("EVIDENCE_RETENTION_EXPIRED", 410)
        with self.storage_factory() as storage:
            document = storage.read(receipt_from(row))
        with self.sessions.begin() as session:
            actor, own = self.authorize(session, asset_id, "read")
            if identity(actor) != before:
                fail("UNAUTHENTICATED", 401)
            current = self.row(session, actor, own, asset_id, evidence_id)
            if not self.metadata(current)["content_available"]:
                fail("EVIDENCE_RETENTION_EXPIRED", 410)
            record_event(
                session,
                request_id=self.request_id,
                organization_id=actor.organization_id,
                actor_user_id=actor.user_id,
                action="evidence.content_read",
                resource_type="evidence",
                resource_id=evidence_id,
                outcome="success",
                details={"version": current["version"]},
            )
        return document

    def submit(self, asset_id, version, key, raw, before):
        # Preflight is repeated before opening directories; never hold get_actor's
        # transaction across configuration, encryption or filesystem operations.
        if self.preflight(asset_id) != before:
            fail("UNAUTHENTICATED", 401)
        with self.storage_factory() as storage:
            writer = EvidenceService(self.sessions, storage)
            receipt = writer.submit(
                self.credentials,
                asset_id=asset_id,
                asset_version=version,
                version=1,
                key=key,
                raw=raw,
                request_id=self.request_id,
                http=True,
            )
        with self.sessions.begin() as session:
            actor, _ = self.authorize(session, asset_id, "write")
            if identity(actor) != before:
                fail("UNAUTHENTICATED", 401)
            evidence_id = session.scalar(
                text(
                    "SELECT id FROM evidence_operations "
                    "WHERE organization_id=:org AND object_id=:object"
                ),
                {"org": actor.organization_id, "object": receipt.object_id},
            )
            row = self.row(session, actor, False, asset_id, evidence_id)
            return self.metadata(row), writer.replayed
