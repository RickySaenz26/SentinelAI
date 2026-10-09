"""Recoverable internal submission. No public route, approval, or target connectivity."""

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from uuid import UUID, uuid4

from sqlalchemy import select, text
from starlette.requests import Request

from app.api.v1.dependencies import SESSION_COOKIE, get_actor
from app.assets.authority import require_admission
from app.core.errors import ApplicationError
from app.evidence.contracts import (
    EvidenceContext,
    canonical_json,
    decode_document,
    document_bytes,
    parse_submission,
)
from app.evidence.coordination import coordinate
from app.evidence.crypto import MAX_ENVELOPE_BYTES
from app.evidence.storage import ObjectReceipt
from app.platform.database.models import Asset
from app.platform.http_idempotency import HttpReplay
from app.platform.outbox import emit_event
from app.security_audit.service import record_event


def fail(code: str, status: int = 409):
    raise ApplicationError(code, "Operación de evidencia no disponible.", status)


@dataclass(frozen=True)
class SessionCredentials:
    token: str = field(repr=False)
    csrf: str = field(repr=False)
    method: str = "POST"
    origin: str | None = None

    def authenticate(self, session):
        cookie = SimpleCookie()
        cookie[SESSION_COOKIE] = self.token
        request = Request(
            {
                "type": "http",
                "method": self.method,
                "path": "/internal/evidence",
                "headers": [(b"cookie", cookie.output(header="").strip().encode("ascii"))]
                + ([(b"origin", self.origin.encode("latin-1"))] if self.origin is not None else []),
            }
        )
        return get_actor(request, csrf_token=self.csrf, session=session)


def receipt_from(row) -> ObjectReceipt:
    return ObjectReceipt(
        row["object_id"],
        EvidenceContext(
            organization_id=row["organization_id"],
            asset_id=row["asset_id"],
            dossier_id=row["asset_id"],
            version=row["version"],
        ),
        row["envelope_sha256"],
    )


class EvidenceService:
    def __init__(self, sessions, storage):
        self.sessions, self.storage = sessions, storage

    @staticmethod
    def commit(session, phase):
        session.commit()

    @contextmanager
    def transaction(self, phase):
        with self.sessions() as session:
            try:
                yield session
                self.commit(session, phase)
            except Exception:
                session.rollback()
                # A successful commit with a lost response must never trigger file deletion.
                raise

    def authorize(self, session, credentials, asset_id, asset_version):
        actor = credentials.authenticate(session)
        if (
            actor.role_code not in {"org_owner", "security_manager", "analyst"}
            or "platform:admin" in actor.permissions
            or "evidence:write" not in actor.permissions
        ):
            fail("FORBIDDEN", 403)
        asset = session.scalar(
            select(Asset)
            .where(Asset.id == asset_id, Asset.organization_id == actor.organization_id)
            .with_for_update()
        )
        if asset is None:
            fail("ASSET_NOT_FOUND", 404)
        if asset.deleted_at is not None or asset.version != asset_version:
            fail("VERSION_CONFLICT")
        published, generation = require_admission(session, actor.organization_id, asset_id)
        return actor, (published.policy.fingerprint, generation)

    @staticmethod
    def quota(session, organization_id):
        return (
            session.execute(
                text("SELECT * FROM evidence_quotas WHERE organization_id=:org FOR UPDATE"),
                {"org": organization_id},
            )
            .mappings()
            .one()
        )

    @staticmethod
    def operation(session, organization_id, operation_id):
        row = (
            session.execute(
                text(
                    "SELECT * FROM evidence_operations "
                    "WHERE organization_id=:org AND id=:id FOR UPDATE"
                ),
                {"org": organization_id, "id": operation_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            fail("EVIDENCE_NOT_FOUND", 404)
        return dict(row)

    @staticmethod
    def current(session, row, actor, policy_hash, state):
        now = session.scalar(text("SELECT clock_timestamp()"))
        if (
            row["state"] != state
            or row["expires_at"] <= now
            or row["actor_id"] != actor.user_id
            or row["session_id"] != actor.session_id
            or row["membership_id"] != actor.membership_id
            or row["admission_generation"] != policy_hash[1]
        ):
            fail("EVIDENCE_RESERVATION_INVALID")

    def reserve(
        self,
        credentials,
        asset_id,
        asset_version,
        version,
        key_hash,
        fingerprint,
        *,
        http_key=None,
        document=None,
    ):
        with self.transaction("reserve") as session:
            actor, policy_hash = self.authorize(session, credentials, asset_id, asset_version)
            org = actor.organization_id
            binding = None
            if http_key is not None:
                binding = HttpReplay(
                    session,
                    actor,
                    key=http_key,
                    method="POST",
                    route=f"/api/v1/evidence/assets/{asset_id}",
                    payload={
                        "document": document.model_dump(mode="json"),
                        "if_match": asset_version,
                    },
                )
                pointer = binding.replay()
                if pointer is not None:
                    previous = self.operation(session, org, UUID(pointer["operation_id"]))
                    if previous["state"] != "committed":
                        fail("EVIDENCE_OPERATION_PENDING")
                    if previous["admission_generation"] != policy_hash[1]:
                        fail("ADMISSION_CHANGED")
                    return previous, True
                # Only new submissions apply observation freshness. A replay has a
                # fixed 24-hour HTTP lifetime, even when its observation is older.
                parse_submission(document_bytes(document), now=datetime.now(UTC))
                key_hash = uuid4().hex * 2
                fingerprint = binding.fingerprint
            session.execute(
                text(
                    "INSERT INTO evidence_quotas(organization_id) VALUES(:org) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"org": org},
            )
            quota = self.quota(session, org)
            previous = (
                session.execute(
                    text(
                        "SELECT * FROM evidence_operations WHERE organization_id=:org "
                        "AND actor_id=:actor AND key_hash=:key"
                    ),
                    {"org": org, "actor": actor.user_id, "key": key_hash},
                )
                .mappings()
                .first()
            )
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    fail("IDEMPOTENCY_CONFLICT")
                if previous["state"] == "committed":
                    if previous["admission_generation"] != policy_hash[1]:
                        fail("ADMISSION_CHANGED")
                    return dict(previous), True
                fail("EVIDENCE_OPERATION_PENDING")
            if (
                quota["reserved_objects"] + quota["used_objects"] >= quota["max_objects"]
                or quota["reserved_bytes"] + quota["used_bytes"] + MAX_ENVELOPE_BYTES
                > quota["max_bytes"]
            ):
                fail("EVIDENCE_QUOTA_EXCEEDED")
            latest = session.scalar(
                text(
                    "SELECT coalesce(max(version),0) FROM evidence_versions "
                    "WHERE organization_id=:org AND asset_id=:asset"
                ),
                {"org": org, "asset": asset_id},
            )
            pending = session.scalar(
                text(
                    "SELECT EXISTS(SELECT FROM evidence_operations WHERE organization_id=:org "
                    "AND asset_id=:asset AND state IN ('reserved','prepared','reclaimed'))"
                ),
                {"org": org, "asset": asset_id},
            )
            if http_key is not None:
                version = latest + 1
            if version != latest + 1 or pending:
                fail("EVIDENCE_VERSION_CONFLICT")
            row = (
                session.execute(
                    text(
                        "INSERT INTO evidence_operations(id,organization_id,asset_id,actor_id,"
                        "membership_id,session_id,asset_version,version,policy_hash,"
                        "key_hash,fingerprint,admission_generation,expires_at) "
                        "VALUES(:id,:org,:asset,:actor,:membership,:session,:asset_version,:version,"
                        ":policy,:key,:fingerprint,:generation,"
                        "clock_timestamp()+interval '5 minutes') "
                        "RETURNING *"
                    ),
                    {
                        "id": uuid4(),
                        "org": org,
                        "asset": asset_id,
                        "actor": actor.user_id,
                        "membership": actor.membership_id,
                        "session": actor.session_id,
                        "asset_version": asset_version,
                        "version": version,
                        "policy": policy_hash[0],
                        "generation": policy_hash[1],
                        "key": key_hash,
                        "fingerprint": fingerprint,
                    },
                )
                .mappings()
                .one()
            )
            session.execute(
                text(
                    "UPDATE evidence_quotas SET reserved_objects=reserved_objects+1,"
                    "reserved_bytes=reserved_bytes+:size WHERE organization_id=:org"
                ),
                {"size": MAX_ENVELOPE_BYTES, "org": org},
            )
            if binding is not None:
                binding.save(
                    asset_id=asset_id, status_code=201, body={"operation_id": str(row["id"])}
                )
            return dict(row), False

    def submit(
        self,
        credentials: SessionCredentials,
        *,
        asset_id: UUID,
        asset_version: int,
        version: int,
        key: str,
        raw: bytes,
        request_id: str,
        http: bool = False,
    ) -> ObjectReceipt:
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", key) is None
        ):
            fail("INVALID_IDEMPOTENCY_KEY", 422)
        if any(
            type(value) is not int or not 1 <= value <= 2**31 - 1
            for value in (version, asset_version)
        ):
            fail("INVALID_EVIDENCE_VERSION", 422)
        document = decode_document(raw) if http else parse_submission(raw, now=datetime.now(UTC))
        fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "asset": str(asset_id),
                    "asset_version": asset_version,
                    "version": version,
                    "document": json.loads(document_bytes(document)),
                }
            )
        ).hexdigest()
        with coordinate(self.storage):
            row, replay = self.reserve(
                credentials,
                asset_id,
                asset_version,
                version,
                hashlib.sha256(key.encode()).hexdigest(),
                fingerprint,
                **({"http_key": key, "document": document} if http else {}),
            )
            self.replayed = replay
            if replay:
                return receipt_from(row)
            version = row["version"]
            context = EvidenceContext(
                organization_id=row["organization_id"],
                asset_id=asset_id,
                dossier_id=asset_id,
                version=version,
            )
            prepared = self.storage.prepare(context, document)
            envelope = json.loads(prepared.encrypted)
            # Persist ownership BEFORE filesystem publication, still without plaintext.
            with self.transaction("prepare") as session:
                actor, policy_hash = self.authorize(session, credentials, asset_id, asset_version)
                self.quota(session, actor.organization_id)
                current = self.operation(session, actor.organization_id, row["id"])
                self.current(session, current, actor, policy_hash, "reserved")
                session.execute(
                    text(
                        "UPDATE evidence_operations SET state='prepared',object_id=:object,"
                        "envelope_sha256=:digest,envelope_bytes=:size,key_id=:key_id,"
                        "wrapped_key=:wrapped,nonce=:nonce,format_version=:format WHERE id=:id"
                    ),
                    {
                        "id": row["id"],
                        "object": prepared.receipt.object_id,
                        "digest": prepared.receipt.envelope_sha256,
                        "size": len(prepared.encrypted),
                        "key_id": envelope["key_id"],
                        "wrapped": envelope["wrapped_key"],
                        "nonce": envelope["nonce"],
                        "format": envelope["format_version"],
                    },
                )
            self.storage.write(prepared)
            with self.transaction("confirm") as session:
                actor, policy_hash = self.authorize(session, credentials, asset_id, asset_version)
                org = actor.organization_id
                self.quota(session, org)
                current = self.operation(session, org, row["id"])
                self.current(session, current, actor, policy_hash, "prepared")
                session.execute(
                    text("UPDATE evidence_operations SET state='committed' WHERE id=:id"),
                    {"id": row["id"]},
                )
                session.execute(
                    text(
                        "INSERT INTO evidence_versions"
                        "(organization_id,asset_id,version,operation_id) "
                        "VALUES(:org,:asset,:version,:id)"
                    ),
                    {"org": org, "asset": asset_id, "version": version, "id": row["id"]},
                )
                session.execute(
                    text(
                        "UPDATE evidence_quotas SET reserved_objects=reserved_objects-1,"
                        "reserved_bytes=reserved_bytes-:reserved,used_objects=used_objects+1,"
                        "used_bytes=used_bytes+:size WHERE organization_id=:org"
                    ),
                    {"reserved": MAX_ENVELOPE_BYTES, "size": current["envelope_bytes"], "org": org},
                )
                record_event(
                    session,
                    request_id=request_id,
                    organization_id=org,
                    actor_user_id=actor.user_id,
                    action="evidence.stored",
                    resource_type="asset",
                    resource_id=asset_id,
                    outcome="success",
                    details={"version": version, "operation_id": str(row["id"])},
                )
                emit_event(
                    session,
                    organization_id=org,
                    aggregate_type="evidence",
                    aggregate_id=row["id"],
                    event_type="evidence.stored",
                    idempotency_key=f"evidence.stored:{row['id']}",
                    payload={"version": version},
                )
            return prepared.receipt
