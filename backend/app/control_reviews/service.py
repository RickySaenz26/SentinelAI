"""Short authenticated DB phases around storage I/O; positive status is never cached."""

import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.assets.authority import require_admission
from app.core.errors import ApplicationError
from app.evidence.access import EvidenceAccess, identity
from app.evidence.errors import EvidenceError
from app.evidence.service import receipt_from
from app.platform.database.models import Asset, HttpIdempotencyRecord


def fail(code, status=409):
    raise ApplicationError(code, "Revisión de control no disponible.", status)


class Replay:
    """Same 05 replay table/contract, with PostgreSQL time and pointers only."""

    def __init__(self, db, actor, key, route, payload, version):
        self.db = db
        self.context = dict(
            organization_id=actor.organization_id,
            actor_id=actor.user_id,
            method="POST",
            route=route,
            key_hash=hashlib.sha256(key.encode()).hexdigest(),
        )
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {"payload": payload, "if_match": version},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        self.now = db.scalar(text("SELECT clock_timestamp()"))
        self.record = db.scalar(select(HttpIdempotencyRecord).filter_by(**self.context))

    def get(self):
        if self.record is None or self.record.expires_at <= self.now:
            return None
        if self.record.fingerprint != self.fingerprint:
            fail("IDEMPOTENCY_CONFLICT")
        return UUID(self.record.response_body["review_id"])

    def save(self, asset_id, review_id, event_id):
        values = dict(
            fingerprint=self.fingerprint,
            asset_id=asset_id,
            status_code=201,
            response_body={"review_id": str(review_id), "event_id": str(event_id)},
            created_at=self.now,
            expires_at=self.now + timedelta(hours=24),
        )
        if self.record is None:
            self.db.add(HttpIdempotencyRecord(**self.context, **values))
        else:
            for key, value in values.items():
                setattr(self.record, key, value)
        self.db.flush()


class ControlReviews:
    def __init__(self, access: EvidenceAccess):
        self.access = access
        self.sessions, self.credentials = access.sessions, access.credentials

    @staticmethod
    def commit(db):
        db.commit()

    @contextmanager
    def transaction(self):
        with self.sessions() as db:
            try:
                yield db
                self.commit(db)
            except Exception:
                db.rollback()
                raise

    def authorize(self, db, action):
        actor = self.credentials.authenticate(db)  # organization lock precedes all row locks
        db.execute(
            text("SELECT set_config('app.control_session',:id,true)"), {"id": str(actor.session_id)}
        )
        db.execute(
            text("SELECT set_config('app.control_user',:id,true)"), {"id": str(actor.user_id)}
        )
        if action == "read" and actor.role_code == "analyst":
            action = "read_own"
        roles = {
            "submit": {"org_owner", "security_manager", "analyst"},
            "read": {"org_owner", "security_manager", "auditor"},
            "read_own": {"analyst"},
            "decide": {"org_owner", "security_manager"},
            "withdraw": {"org_owner", "security_manager", "analyst"},
            "revoke": {"org_owner", "security_manager"},
            "summary": {
                "org_owner",
                "security_manager",
                "analyst",
                "auditor",
                "viewer",
                "platform_admin",
            },
        }
        if actor.role_code not in roles[action] or f"control:{action}" not in actor.permissions:
            fail("FORBIDDEN", 403)
        if action == "decide" and "evidence:read" not in actor.permissions:
            fail("FORBIDDEN", 403)
        return actor

    @staticmethod
    def asset(db, actor, asset_id):
        row = db.scalar(
            select(Asset)
            .where(Asset.organization_id == actor.organization_id, Asset.id == asset_id)
            .with_for_update()
        )
        if row is None:
            fail("ASSET_NOT_FOUND", 404)
        return row

    @staticmethod
    def row(db, actor, review_id):
        result = (
            db.execute(
                text(
                    "SELECT r.*,p.version,p.state,p.decided_at,p.valid_until,p.revoked_at,"
                    "p.invalidated_at,p.superseded_by FROM control_review_requests r "
                    "JOIN control_review_projections p ON (p.organization_id,p.review_id)="
                    "(r.organization_id,r.id) WHERE r.organization_id=:org AND r.id=:id"
                ),
                {"org": actor.organization_id, "id": review_id},
            )
            .mappings()
            .first()
        )
        if result is None:
            fail("CONTROL_NOT_FOUND", 404)
        return dict(result)

    def document(self, db, actor, row):
        events = (
            db.execute(
                text(
                    "SELECT * FROM control_review_events WHERE organization_id=:org "
                    "AND review_id=:id ORDER BY occurred_at,id"
                ),
                {"org": actor.organization_id, "id": row["id"]},
            )
            .mappings()
            .all()
        )
        return {**row, "events": [dict(e) for e in events], "request_id": self.access.request_id}

    def evidence(self, db, actor, asset_id, evidence_id, version, *, own):
        row = self.access.row(db, actor, own, asset_id, evidence_id)
        now = db.scalar(text("SELECT clock_timestamp()"))
        if row["state"] != "committed" or row["version"] != version:
            fail("CONTROL_EVIDENCE_REFERENCE", 404)
        if row["submitted_at"] + timedelta(days=90) <= now:
            fail("EVIDENCE_RETENTION_EXPIRED", 410)
        return row

    def read_storage(self, row):
        # Deliberately NOT EvidenceAccess.content: a validation is not a human reading receipt.
        with self.access.storage_factory() as storage:
            storage.read(receipt_from(row))

    def mutate(self, asset_id, review_id, action, payload, version, key, route):
        data = payload.model_dump(mode="json")
        with self.transaction() as db:
            actor = self.authorize(db, action)
            review = self.row(db, actor, review_id) if review_id else None
            asset_id = review["asset_id"] if review else asset_id
            self.asset(db, actor, asset_id)
            replay = Replay(db, actor, key, route, data, version)
            prior = replay.get()
            if prior:
                return self.document(db, actor, self.row(db, actor, prior)), True
            before = identity(actor)
            generation = None
            if action == "submit":
                generation = require_admission(db, actor.organization_id, asset_id)[1]
            if action in {"submit", "decide"}:
                evidence_id = review["evidence_id"] if review else payload.evidence_id
                evidence_version = (
                    review["evidence_version"] if review else payload.evidence_version
                )
                evidence = self.evidence(
                    db, actor, asset_id, evidence_id, evidence_version, own=action == "submit"
                )
            else:
                evidence = None
        if evidence:
            self.read_storage(evidence)
        with self.transaction() as db:
            actor = self.authorize(db, action)
            if identity(actor) != before:
                fail("UNAUTHENTICATED", 401)
            asset = self.asset(db, actor, asset_id)
            if review_id:
                review = self.row(db, actor, review_id)
                # The INSERT trigger locks the projection as its owner. Runtime must
                # not receive UPDATE privileges merely to issue SELECT FOR UPDATE.
            replay = Replay(db, actor, key, route, data, version)
            prior = replay.get()
            if prior:
                return self.document(db, actor, self.row(db, actor, prior)), True
            if generation is not None:
                require_admission(db, actor.organization_id, asset_id, generation=generation)
            if evidence:
                current = self.evidence(
                    db, actor, asset_id, evidence["id"], evidence["version"], own=action == "submit"
                )
                if current != evidence:
                    fail("CONTROL_EVIDENCE_CHANGED")
            values = {"org": actor.organization_id, "id": uuid4()}
            if action == "submit":
                review_id = values["id"]
                db.execute(
                    text(
                        "INSERT INTO control_review_requests(organization_id,id,asset_id,"
                        "evidence_id,evidence_version,asset_version,renews_review_id) "
                        "VALUES(:org,:id,:asset,:evidence,:ev,:version,:parent)"
                    ),
                    {
                        **values,
                        "asset": asset.id,
                        "evidence": payload.evidence_id,
                        "ev": payload.evidence_version,
                        "version": version,
                        "parent": payload.renews_review_id,
                    },
                )
                event_id = db.scalar(
                    text(
                        "SELECT id FROM control_review_events "
                        "WHERE organization_id=:org AND review_id=:id "
                        "AND kind='submitted'"
                    ),
                    values,
                )
            else:
                event_id = values["id"]
                kind = (
                    payload.decision
                    if action == "decide"
                    else {"withdraw": "withdrawn", "revoke": "revoked"}[action]
                )
                db.execute(
                    text(
                        "INSERT INTO control_review_events(organization_id,id,review_id,"
                        "kind,expected_version,reason_code,checklist_version,checklist) "
                        "VALUES(:org,:id,:review,:kind,:version,:reason,:cv,CAST(:ck AS jsonb))"
                    ),
                    {
                        **values,
                        "review": review_id,
                        "kind": kind,
                        "version": version,
                        "reason": payload.reason_code,
                        "cv": data.get("checklist_version"),
                        "ck": json.dumps(data["checklist"]) if "checklist" in data else None,
                    },
                )
            replay.save(asset_id, review_id, event_id)
            return self.document(db, actor, self.row(db, actor, review_id)), False

    def get(self, review_id):
        with self.transaction() as db:
            actor = self.authorize(db, "read")
            return self.document(db, actor, self.row(db, actor, review_id))

    def listing(self, asset_id, limit, after):
        with self.transaction() as db:
            actor = self.authorize(db, "read")
            self.asset(db, actor, asset_id)
            ids = db.scalars(
                text(
                    "SELECT id FROM control_review_requests WHERE organization_id=:org "
                    "AND asset_id=:asset AND (CAST(:after AS uuid) IS NULL OR id>:after) "
                    "ORDER BY id LIMIT :limit"
                ),
                {
                    "org": actor.organization_id,
                    "asset": asset_id,
                    "after": after,
                    "limit": limit + 1,
                },
            ).all()
            return {
                "items": [
                    self.document(db, actor, self.row(db, actor, key)) for key in ids[:limit]
                ],
                "next_id": ids[limit - 1] if len(ids) > limit else None,
                "request_id": self.access.request_id,
            }

    def summary(self, asset_id):
        with self.transaction() as db:
            actor = self.authorize(db, "summary")
            self.asset(db, actor, asset_id)
            count = db.scalar(text("SELECT control_summary(:asset)"), {"asset": asset_id})
            return {"asset_id": asset_id, "review_count": count}

    @staticmethod
    def reasons(db, actor, asset, row):
        now = db.scalar(text("SELECT clock_timestamp()"))
        if row is None:
            return now, ["no_approval"]
        reasons = []
        for field, reason in (
            ("revoked_at", "revoked"),
            ("superseded_by", "superseded"),
            ("invalidated_at", "invalidated"),
        ):
            if row[field] is not None:
                reasons.append(reason)
        if now >= min(row["valid_until"], row["retention_until"]):
            reasons.append("expired")
        snapshot = row["snapshot"]
        if (
            asset.deleted_at is not None
            or asset.version < row["asset_version"]
            or any(
                getattr(asset, field) != snapshot[field]
                for field in ("type", "canonical_target", "ownership_status")
            )
        ):
            reasons.append("asset_incompatible")
        generation = db.execute(
            text(
                "SELECT c.generation,h.admitted FROM asset_admission_current c "
                "JOIN asset_admission_history h ON (h.organization_id,h.asset_id,"
                "h.generation)=(c.organization_id,c.asset_id,c.generation) "
                "WHERE c.organization_id=:org AND c.asset_id=:asset"
            ),
            {"org": actor.organization_id, "asset": asset.id},
        ).first()
        if generation is None or generation[0] != row["generation"] or not generation[1]:
            reasons.append("admission_changed")
        return now, reasons

    def status(self, asset_id):
        with self.transaction() as db:
            actor = self.authorize(db, "read")
            asset = self.asset(db, actor, asset_id)
            key = db.scalar(
                text(
                    "SELECT review_id FROM control_review_projections "
                    "WHERE organization_id=:org AND asset_id=:asset AND state='approved' "
                    "ORDER BY decided_at DESC,review_id DESC LIMIT 1"
                ),
                {"org": actor.organization_id, "asset": asset_id},
            )
            row = self.row(db, actor, key) if key else None
            now, reasons = self.reasons(db, actor, asset, row)
            before = identity(actor)
            evidence = None
            if not reasons:
                evidence = self.evidence(
                    db,
                    actor,
                    asset_id,
                    row["evidence_id"],
                    row["evidence_version"],
                    own=actor.role_code == "analyst",
                )
        if evidence:
            try:
                self.read_storage(evidence)
            except (EvidenceError, OSError, ValueError):
                reasons.append("evidence_unavailable")
            with self.transaction() as db:
                actor = self.authorize(db, "read")
                if identity(actor) != before:
                    fail("UNAUTHENTICATED", 401)
                asset = self.asset(db, actor, asset_id)
                row = self.row(db, actor, key)
                now, current_reasons = self.reasons(db, actor, asset, row)
                reasons = list(dict.fromkeys(reasons + current_reasons))
        return {
            "asset_id": asset_id,
            "review_id": key,
            "valid": not reasons,
            "checked_at": now,
            "reasons": reasons,
        }
