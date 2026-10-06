"""Real PostgreSQL and private Linux tmpfs; no target connectivity or persistent keys."""

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core.errors import ApplicationError
from app.evidence import maintenance as maintenance_module
from app.evidence import service as service_module
from app.evidence.configuration import configured_storage
from app.evidence.contracts import EvidenceContext, canonical_json, parse_submission
from app.evidence.coordination import EvidenceBusy, coordinate
from app.evidence.crypto import MAX_ENVELOPE_BYTES
from app.evidence.errors import EvidenceUnavailable, StorageInterrupted, UnsafeStorage
from app.evidence.maintenance import EvidenceMaintenance
from app.evidence.service import EvidenceService, SessionCredentials, receipt_from
from app.evidence.storage import LocalEvidenceStorage
from app.platform.database.session import set_organization_context
from tests.test_assets import POLICY, create


@pytest.fixture(scope="module")
def maintenance_engine(admin_engine):
    # Explicit operational LOGIN with no app identity privileges. Never use migrator for cleanup.
    name = "evidence_test_" + uuid4().hex
    password = uuid4().hex
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOBYPASSRLS INHERIT"
        )
        connection.exec_driver_sql(f"GRANT sentinelai_evidence_maintenance TO {name}")
    engine = create_engine(admin_engine.url.set(username=name, password=password))
    yield engine
    engine.dispose()
    with admin_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM evidence_maintenance_tenants WHERE role_name=:name"), {"name": name}
        )
        connection.exec_driver_sql(f"DROP ROLE {name}")


@pytest.fixture
def setup(client, login, seeded, runtime_engine, maintenance_engine, admin_engine, monkeypatch):
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evidence_maintenance_tenants(role_name,organization_id) "
                "VALUES(:role,:org)"
            ),
            {"role": maintenance_engine.url.username, "org": seeded["org_a"]},
        )
    monkeypatch.setenv("LAB_ASSET_POLICY_JSON", json.dumps(POLICY))
    auth = login()
    response = create(client, auth)
    assert response.status_code == 201, response.text
    asset = response.json()
    # No repository paths, production mount or shared storage are touched.
    with tempfile.TemporaryDirectory(prefix="sentinelai-evidence-stage2-") as directory:
        root = Path(directory)
        for name in ("storage", "keys", "repository"):
            (root / name).mkdir(mode=0o700)
        key = root / "keys" / "lab-1.kek"
        key.write_bytes(os.urandom(32))
        key.chmod(0o600)
        environment = {
            "LAB_EVIDENCE_ROOT": str(root / "storage"),
            "LAB_EVIDENCE_KEY_ROOT": str(root / "keys"),
            "LAB_EVIDENCE_ACTIVE_KEY_ID": "lab-1",
        }
        with configured_storage(environment, repository_root=root / "repository") as storage:
            sessions = sessionmaker(runtime_engine, autoflush=False, expire_on_commit=False)
            service = EvidenceService(sessions, storage)
            maintenance = EvidenceMaintenance(
                sessionmaker(maintenance_engine), storage, organization_id=seeded["org_a"]
            )
            body = {
                "schema_version": 1,
                "method": "supervised_local_console",
                "observed_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "lab_asset_reference": "LAB-123456",
                "observations": {
                    name: "observed"
                    for name in (
                        "console_identified",
                        "inventory_ipv4_matches",
                        "administrative_control",
                    )
                },
                "declaration": "technical_control_only_not_ownership_or_scan_permission",
            }
            arguments = dict(
                asset_id=UUID(asset["id"]),
                asset_version=asset["version"],
                version=1,
                key="submission-1",
                raw=canonical_json(body),
                request_id="evidence-test",
            )
            yield (
                service,
                SessionCredentials(auth["token"], auth["csrf"]),
                arguments,
                maintenance,
                root,
            )
    assert not root.exists(), "Owned ephemeral evidence and keys must be removed"


def snapshot(admin_engine):
    with admin_engine.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM evidence_operations")).mappings()
        ]
        quota = connection.execute(text("SELECT * FROM evidence_quotas")).mappings().first()
        counts = tuple(
            connection.scalar(text(query))
            for query in (
                "SELECT count(*) FROM evidence_versions",
                "SELECT count(*) FROM security_audit_events WHERE action='evidence.stored'",
                "SELECT count(*) FROM outbox_events WHERE event_type='evidence.stored'",
            )
        )
    return rows, quota, counts


def expire(admin_engine):
    # Fault injection only in guarded closure_test databases; restore trigger atomically.
    with admin_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE evidence_operations DISABLE TRIGGER trg_evidence_operation")
        )
        connection.execute(
            text(
                "UPDATE evidence_operations SET "
                "created_at=clock_timestamp()-interval '10 minutes', "
                "expires_at=clock_timestamp()-interval '1 minute' WHERE state<>'committed'"
            )
        )
        connection.execute(
            text("ALTER TABLE evidence_operations ENABLE TRIGGER trg_evidence_operation")
        )


def test_success_replay_versions_and_no_plaintext(setup, admin_engine):
    service, credentials, args, maintenance, root = setup
    receipt = service.submit(credentials, **args)
    assert service.storage.read(receipt).lab_asset_reference == "LAB-123456"
    assert service.submit(credentials, **args) == receipt
    rows, quota, counts = snapshot(admin_engine)
    assert counts == (1, 1, 1)
    assert quota["reserved_objects"] == quota["reserved_bytes"] == 0
    assert quota["used_objects"] == 1 and quota["used_bytes"] == rows[0]["envelope_bytes"]
    assert rows[0]["state"] == "committed"
    assert "LAB-123456" not in repr(rows)
    assert credentials.token not in repr(credentials)
    for path in (root / "storage").iterdir():
        assert b"LAB-123456" not in path.read_bytes()
    (root / "storage" / "unknown.evidence").write_bytes(b"unknown")
    assert maintenance.run(execute=True)["unknown_preserved"] == 1
    assert maintenance.claim(rows[0]["id"]) is None
    assert service.storage.read(receipt)
    assert (root / "storage" / "unknown.evidence").read_bytes() == b"unknown"
    second = service.submit(credentials, **{**args, "version": 2, "key": "submission-2"})
    assert second.object_id != receipt.object_id
    assert snapshot(admin_engine)[2] == (2, 2, 2)

    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT ownership_status FROM assets")) == "unverified"
    with pytest.raises(ApplicationError) as conflict:
        service.submit(credentials, **{**args, "version": 2})
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    "field,value",
    [
        ("key", ""),
        ("key", None),
        ("version", True),
        ("version", 0),
        ("asset_version", False),
        ("version", 2**31),
    ],
)
def test_invalid_arguments_do_not_reserve(setup, admin_engine, field, value):
    service, credentials, args, _, _ = setup
    with pytest.raises(ApplicationError):
        service.submit(credentials, **{**args, field: value})
    assert snapshot(admin_engine)[0] == []


@pytest.mark.parametrize(
    "kind", ["csrf", "session", "permission", "asset", "version", "policy", "next-version"]
)
def test_reservation_revalidates_inputs(setup, admin_engine, login, monkeypatch, kind):
    service, credentials, args, _, _ = setup
    if kind == "csrf":
        credentials = SessionCredentials(credentials.token, "invalid")
    elif kind == "session":
        credentials = SessionCredentials("invalid", credentials.csrf)
    elif kind == "permission":
        auth = login("platform_admin")
        credentials = SessionCredentials(auth["token"], auth["csrf"])
    elif kind == "asset":
        args = {**args, "asset_id": uuid4()}
    elif kind == "version":
        args = {**args, "asset_version": 2}
    elif kind == "next-version":
        args = {**args, "version": 2}
    else:
        monkeypatch.delenv("LAB_ASSET_POLICY_JSON")
    with pytest.raises(ApplicationError):
        service.submit(credentials, **args)
    assert snapshot(admin_engine)[0] == []


@pytest.mark.parametrize(
    "phase,after_commit",
    [
        ("reserve", False),
        ("reserve", True),
        ("prepare", False),
        ("prepare", True),
        ("confirm", False),
        ("confirm", True),
    ],
)
def test_uncertain_commit_preserves_and_retry_never_duplicates(
    setup, admin_engine, monkeypatch, phase, after_commit
):
    service, credentials, args, maintenance, root = setup

    def commit(session, current):
        if current != phase or after_commit:
            session.commit()
        if current == phase:
            raise OSError("simulated lost commit response")

    with monkeypatch.context() as patch:
        patch.setattr(service, "commit", commit)
        with pytest.raises(OSError):
            service.submit(credentials, **args)
    rows, _, counts = snapshot(admin_engine)
    files = list((root / "storage").glob("*.evidence"))
    assert bool(files) == (phase == "confirm")
    if phase == "confirm" and after_commit:
        receipt = service.submit(credentials, **args)
        assert service.storage.read(receipt)
        assert counts == snapshot(admin_engine)[2] == (1, 1, 1)
        assert maintenance.run(execute=True)["cleaned"] == 0
    elif not rows:
        service.submit(credentials, **args)
        assert snapshot(admin_engine)[2] == (1, 1, 1)
    else:
        assert counts == (0, 0, 0)
        with pytest.raises(ApplicationError) as pending:
            service.submit(credentials, **args)
        assert pending.value.code == "EVIDENCE_OPERATION_PENDING"
        assert maintenance.claim(rows[0]["id"]) is None
        expire(admin_engine)
        assert maintenance.run()["candidates"] == 1
        assert snapshot(admin_engine)[0][0]["state"] != "reclaimed"
        assert maintenance.run(execute=True)["cleaned"] == 1
        assert snapshot(admin_engine)[0][0]["state"] == "aborted"
        assert snapshot(admin_engine)[1]["reserved_bytes"] == 0
        assert not list((root / "storage").glob("*.evidence"))
        assert maintenance.claim(rows[0]["id"]) is None


@pytest.mark.parametrize("fault", ["write", "publish", "audit", "outbox"])
def test_storage_and_event_failures_roll_back_acceptance(setup, admin_engine, monkeypatch, fault):
    service, credentials, args, maintenance, root = setup

    def fail(*a, **kw):
        raise OSError("injected failure")

    with monkeypatch.context() as patch:
        if fault == "write":
            patch.setattr(service.storage, "write_all", fail)
        elif fault == "publish":
            patch.setattr(os, "link", fail)
        else:
            patch.setattr(
                service_module, "record_event" if fault == "audit" else "emit_event", fail
            )
        with pytest.raises((OSError, StorageInterrupted)):
            service.submit(credentials, **args)
    rows, quota, counts = snapshot(admin_engine)
    assert rows[0]["state"] == "prepared" and counts == (0, 0, 0)
    assert quota["reserved_objects"] == 1 and quota["used_objects"] == 0
    expire(admin_engine)
    assert maintenance.run(execute=True)["cleaned"] == 1
    assert not list((root / "storage").glob("*.tmp"))
    assert not list((root / "storage").glob("*.evidence"))


@pytest.mark.parametrize(
    "change", ["session", "membership", "archive", "policy", "policy-hash", "expiry"]
)
def test_authorization_change_during_io_prevents_confirmation(
    setup, admin_engine, monkeypatch, change
):
    service, credentials, args, maintenance, root = setup
    original = service.storage.write

    def write(prepared):
        receipt = original(prepared)
        with admin_engine.begin() as connection:
            if change == "session":
                connection.execute(text("UPDATE sessions SET revoked_at=clock_timestamp()"))
            elif change == "membership":
                connection.execute(
                    text(
                        "UPDATE memberships SET status='suspended' "
                        "WHERE id IN (SELECT membership_id FROM evidence_operations)"
                    )
                )
            elif change == "archive":
                connection.execute(
                    text(
                        "UPDATE assets SET deleted_at=clock_timestamp(),"
                        "archive_reason='test',version=version+1"
                    )
                )
        if change == "policy":
            monkeypatch.delenv("LAB_ASSET_POLICY_JSON")
        elif change == "policy-hash":
            monkeypatch.setenv(
                "LAB_ASSET_POLICY_JSON", json.dumps({**POLICY, "max_active_assets_per_tenant": 3})
            )
        elif change == "expiry":
            expire(admin_engine)
        return receipt

    monkeypatch.setattr(service.storage, "write", write)
    with pytest.raises(ApplicationError):
        service.submit(credentials, **args)
    assert snapshot(admin_engine)[2] == (0, 0, 0)
    assert len(list((root / "storage").glob("*.evidence"))) == 1
    expire(admin_engine)
    assert maintenance.run(execute=True)["cleaned"] == 1


def test_blocked_io_holds_no_db_locks_and_excludes_competing_writer_and_reconciler(
    setup, admin_engine, monkeypatch, seeded
):
    service, credentials, args, maintenance, _ = setup
    entered, release = Event(), Event()
    original = service.storage.write

    def write(prepared):
        entered.set()
        assert release.wait(15)
        return original(prepared)

    monkeypatch.setattr(service.storage, "write", write)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.submit, credentials, **args)
        try:
            assert entered.wait(15)
            with admin_engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='1s'"))
                assert connection.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:org,1))"),
                    {"org": str(seeded["org_a"])},
                )
                for table in (
                    "assets",
                    "memberships",
                    "sessions",
                    "evidence_quotas",
                    "evidence_operations",
                ):
                    connection.execute(text(f"SELECT * FROM {table} FOR UPDATE NOWAIT"))
            expire(admin_engine)
            assert maintenance.run()["candidates"] == 1
            with pytest.raises(EvidenceBusy):
                maintenance.run(execute=True)
            other = LocalEvidenceStorage(
                setup[4] / "storage", service.storage.codec, repository_root=setup[4] / "repository"
            )
            try:
                competitor = EvidenceService(service.sessions, other)
                with pytest.raises(EvidenceBusy):
                    competitor.submit(credentials, **{**args, "key": "concurrent"})
            finally:
                other.close()
        finally:
            release.set()
        with pytest.raises(ApplicationError):
            future.result(timeout=15)
    assert maintenance.run(execute=True)["cleaned"] == 1


@pytest.mark.parametrize("limit", ["max_objects", "max_bytes"])
def test_quota_limits_and_pending_reservations(setup, admin_engine, monkeypatch, limit):
    service, credentials, args, _, _ = setup
    original = service.storage.prepare
    with monkeypatch.context() as patch:

        def failure(*a):
            raise OSError("before encryption")

        patch.setattr(service.storage, "prepare", failure)
        with pytest.raises(OSError):
            service.submit(credentials, **args)
    with pytest.raises(ApplicationError) as pending:
        service.submit(credentials, **{**args, "key": "another"})
    assert pending.value.code == "EVIDENCE_VERSION_CONFLICT"
    with admin_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE evidence_quotas SET {limit}=:limit"),
            {"limit": 1 if limit == "max_objects" else MAX_ENVELOPE_BYTES},
        )
    with pytest.raises(ApplicationError) as quota:
        service.submit(credentials, **{**args, "key": "another"})
    assert quota.value.code == "EVIDENCE_QUOTA_EXCEEDED"
    assert service.storage.prepare == original


def test_reclaimed_fence_cleanup_retry_and_conservative_unknowns(setup, admin_engine, monkeypatch):
    service, credentials, args, maintenance, root = setup
    with monkeypatch.context() as patch:
        patch.setattr(
            service_module, "emit_event", lambda *a, **kw: (_ for _ in ()).throw(OSError())
        )
        with pytest.raises(OSError):
            service.submit(credentials, **args)
    row = snapshot(admin_engine)[0][0]
    expire(admin_engine)
    with coordinate(service.storage):
        claimed = maintenance.claim(row["id"])
        assert claimed is not None
        with service.sessions.begin() as session:
            set_organization_context(session, row["organization_id"])
            with pytest.raises(DBAPIError):
                session.execute(
                    text("UPDATE evidence_operations SET state='committed' WHERE id=:id"), row
                )
    assert snapshot(admin_engine)[0][0]["state"] == "reclaimed"
    with monkeypatch.context() as patch:
        patch.setattr(maintenance, "release", lambda *a: (_ for _ in ()).throw(OSError()))
        with pytest.raises(OSError):
            maintenance.run(execute=True)
    assert not list((root / "storage").glob("*.evidence"))
    assert snapshot(admin_engine)[1]["reserved_objects"] == 1
    assert maintenance.run(execute=True)["cleaned"] == 1
    assert snapshot(admin_engine)[1]["reserved_objects"] == 0
    with pytest.raises(UnsafeStorage):
        maintenance.release(row["id"])


@pytest.mark.parametrize("corruption", ["content", "conflicting-temp", "same-inode"])
def test_recovery_validates_owned_files_before_removal(
    setup, admin_engine, monkeypatch, corruption
):
    service, credentials, args, maintenance, root = setup
    with monkeypatch.context() as patch:
        patch.setattr(
            service_module, "emit_event", lambda *a, **kw: (_ for _ in ()).throw(OSError())
        )
        with pytest.raises(OSError):
            service.submit(credentials, **args)
    row = snapshot(admin_engine)[0][0]
    temporary, final = (
        root / "storage" / name for name in service.storage.names(receipt_from(row))
    )
    if corruption == "content":
        final.write_bytes(b"corrupt")
    elif corruption == "conflicting-temp":
        temporary.write_bytes(b"other")
    else:
        os.link(final, temporary)
    expire(admin_engine)
    if corruption == "same-inode":
        assert maintenance.run(execute=True)["cleaned"] == 1
        assert not temporary.exists() and not final.exists()
    else:
        with pytest.raises((UnsafeStorage, EvidenceUnavailable)):
            maintenance.run(execute=True)
        assert final.exists()
        assert snapshot(admin_engine)[0][0]["state"] == "reclaimed"


def test_maintenance_rejects_web_and_admin_credentials(setup, runtime_engine, admin_engine, seeded):
    _, _, _, maintenance, _ = setup
    for engine in (runtime_engine, admin_engine):
        rejected = EvidenceMaintenance(
            sessionmaker(engine), maintenance.storage, organization_id=seeded["org_a"]
        )
        with pytest.raises(UnsafeStorage):
            rejected.run()
    maintenance.organization_id = "not-a-uuid"
    with pytest.raises(UnsafeStorage):
        maintenance.run()


def test_rls_composite_fks_and_minimum_grants(
    setup, runtime_engine, maintenance_engine, admin_engine, seeded
):
    service, credentials, args, maintenance, _ = setup
    receipt = service.submit(credentials, **args)
    row = snapshot(admin_engine)[0][0]
    with runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(seeded["org_b"])},
        )
        for table in ("evidence_operations", "evidence_versions", "evidence_quotas"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    with service.sessions() as session:
        set_organization_context(session, seeded["org_b"])
        with pytest.raises(ApplicationError) as missing:
            service.operation(session, seeded["org_b"], row["id"])
        assert missing.value.status_code == 404
    for engine, commands in (
        (
            runtime_engine,
            [
                "UPDATE evidence_operations SET fingerprint=repeat('b',64)",
                "DELETE FROM evidence_operations",
                "UPDATE evidence_quotas SET max_objects=9999",
                "UPDATE evidence_versions SET version=2",
                "DELETE FROM evidence_versions",
                "UPDATE evidence_operations SET state='aborted'",
                "UPDATE evidence_operations SET nonce='AAAAAAAAAAAAAAAA'",
            ],
        ),
        (
            maintenance_engine,
            [
                "SELECT * FROM sessions",
                "UPDATE evidence_operations SET envelope_bytes=42",
                "UPDATE evidence_quotas SET used_objects=0",
                "DELETE FROM evidence_versions",
            ],
        ),
    ):
        for command in commands:
            with engine.connect() as connection:
                connection.execute(
                    text("SELECT set_config('app.organization_id',:org,true)"),
                    {"org": str(seeded["org_a"])},
                )
                with pytest.raises(DBAPIError):
                    connection.execute(text(command))
                connection.rollback()
    assert service.storage.read(receipt)
    maintenance.organization_id = seeded["org_b"]
    with pytest.raises(UnsafeStorage, match="not provisioned"):
        maintenance.run(execute=True)
    with maintenance_engine.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(seeded["org_b"])},
        )
        assert connection.scalar(text("SELECT count(*) FROM evidence_operations")) == 0
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO evidence_maintenance_tenants(role_name,organization_id) "
                    "VALUES(current_user,:org)"
                ),
                {"org": seeded["org_b"]},
            )
        connection.rollback()


@pytest.fixture
def unreferenced_committed_operation(setup, admin_engine):
    # FK fixture only: keep all triggers/checks enabled and never insert a reference.
    service, credentials, args, _, _ = setup
    row, replay = service.reserve(
        credentials, args["asset_id"], args["asset_version"], args["version"], "a" * 64, "b" * 64
    )
    assert not replay
    context = EvidenceContext(
        organization_id=row["organization_id"],
        asset_id=row["asset_id"],
        dossier_id=row["asset_id"],
        version=row["version"],
    )
    prepared = service.storage.prepare(
        context, parse_submission(args["raw"], now=datetime.now(UTC))
    )
    envelope = json.loads(prepared.encrypted)
    service.storage.write(prepared)
    with admin_engine.begin() as connection:
        connection.execute(
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
        connection.execute(
            text("UPDATE evidence_operations SET state='committed' WHERE id=:id"), {"id": row["id"]}
        )
        connection.execute(
            text(
                "UPDATE evidence_quotas SET reserved_objects=reserved_objects-1,"
                "reserved_bytes=reserved_bytes-:reserved,used_objects=used_objects+1,"
                "used_bytes=used_bytes+:size WHERE organization_id=:org"
            ),
            {
                "reserved": MAX_ENVELOPE_BYTES,
                "size": len(prepared.encrypted),
                "org": row["organization_id"],
            },
        )
    assert service.storage.read(prepared.receipt)
    return row


@pytest.mark.parametrize("substitution", ["tenant", "asset", "version"])
def test_reference_substitution_reaches_composite_fk(
    unreferenced_committed_operation, admin_engine, seeded, substitution
):
    row = unreferenced_committed_operation
    insert_reference = text(
        "INSERT INTO evidence_versions(organization_id,asset_id,version,operation_id) "
        "VALUES(:org,:asset,:version,:operation)"
    )
    valid = {
        "org": row["organization_id"],
        "asset": row["asset_id"],
        "version": row["version"],
        "operation": row["id"],
    }
    attempted = dict(valid)
    if substitution in ("tenant", "asset"):
        attempted["asset"] = uuid4()
        if substitution == "tenant":
            attempted["org"] = seeded["org_b"]
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO assets(id,organization_id,canonical_target,display_name,"
                    "criticality,policy_hash) "
                    "VALUES(:asset,:org,'192.0.2.11','FK fixture','low',:hash)"
                ),
                {**attempted, "hash": row["policy_hash"]},
            )
    else:
        attempted["version"] += 1

    # Positive control proves the operation satisfies every reference constraint.
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM evidence_versions")) == 0
        connection.execute(insert_reference, valid)
        assert connection.scalar(text("SELECT count(*) FROM evidence_versions")) == 1
        connection.rollback()
    # Fresh transaction: neither operation UNIQUE nor the asset FK can mask this FK.
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM evidence_versions")) == 0
        assert (
            connection.scalar(
                text("SELECT count(*) FROM assets WHERE organization_id=:org AND id=:asset"),
                attempted,
            )
            == 1
        )
        with pytest.raises(DBAPIError) as failed:
            connection.execute(insert_reference, attempted)
        assert failed.value.orig.sqlstate == "23503"
        assert failed.value.orig.diag.constraint_name == (
            "evidence_versions_organization_id_operation_id_asset_id_ve_fkey"
        )
        connection.rollback()


def test_maintenance_cli_inspection_and_sanitized_failure(
    setup, maintenance_engine, monkeypatch, capsys
):
    _, _, _, maintenance, root = setup
    monkeypatch.setenv(
        "LAB_EVIDENCE_MAINTENANCE_DATABASE_URL",
        maintenance_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("LAB_EVIDENCE_ROOT", str(root / "storage"))
    monkeypatch.setenv("LAB_EVIDENCE_KEY_ROOT", str(root / "keys"))
    monkeypatch.setenv("LAB_EVIDENCE_ACTIVE_KEY_ID", "lab-1")
    arguments = [
        "--organization",
        str(maintenance.organization_id),
        "--repository-root",
        str(root / "repository"),
    ]
    assert maintenance_module.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["execute"] is False
    monkeypatch.setenv("LAB_EVIDENCE_ACTIVE_KEY_ID", "missing")
    assert maintenance_module.main(arguments) == 1
    assert "Evidence maintenance failed" in capsys.readouterr().out
    monkeypatch.delenv("LAB_EVIDENCE_MAINTENANCE_DATABASE_URL")
    assert maintenance_module.main(arguments) == 1


def test_same_replay_key_is_scoped_by_authenticated_tenant(
    setup, client, create_user, seeded, admin_engine
):
    service, credentials, args, _, _ = setup
    first = service.submit(credentials, **args)
    owner = create_user(role="org_owner", organization_id=seeded["org_b"])
    response = client.post(
        "/api/v1/session", json={"email": owner["email"], "password": owner["password"]}
    )
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    from app.core.config import get_settings

    auth = {"headers": {get_settings().csrf_header_name: csrf}}
    second_credentials = SessionCredentials(client.cookies.get("__Host-sentinel_session"), csrf)
    with pytest.raises(ApplicationError) as denied:
        service.submit(second_credentials, **args)
    assert denied.value.code == "ASSET_NOT_FOUND"
    response = create(client, auth, key="create-second")
    assert response.status_code == 201
    second = service.submit(second_credentials, **{**args, "asset_id": UUID(response.json()["id"])})
    assert first.context.organization_id != second.context.organization_id
    assert first.object_id != second.object_id
    assert snapshot(admin_engine)[2] == (2, 2, 2)
    # A real second-tenant row exists: forging the GUC still gives maintenance no access.
    with setup[3].sessions() as session:
        set_organization_context(session, seeded["org_b"])
        for table in ("evidence_operations", "evidence_versions", "evidence_quotas"):
            assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert (
            session.execute(text("UPDATE evidence_operations SET state='reclaimed'")).rowcount == 0
        )
        assert session.execute(text("UPDATE evidence_quotas SET reserved_objects=0")).rowcount == 0


def test_database_fks_reject_mixed_identities_and_unaccepted_references(
    setup, admin_engine, monkeypatch, seeded
):
    service, credentials, args, _, _ = setup
    with monkeypatch.context() as patch:
        patch.setattr(
            service_module, "emit_event", lambda *a, **kw: (_ for _ in ()).throw(OSError())
        )
        with pytest.raises(OSError):
            service.submit(credentials, **args)
    row = snapshot(admin_engine)[0][0]
    other_asset = uuid4()
    # Maintenance can reclaim/abort, but cannot impersonate the confirming writer.
    with setup[3].sessions() as session:
        setup[3].scope(session)
        with pytest.raises(DBAPIError) as failed:
            session.execute(text("UPDATE evidence_operations SET state='committed'"))
        assert failed.value.orig.sqlstate == "42501"
        session.rollback()
    with admin_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO evidence_quotas(organization_id) VALUES(:org)"),
            {"org": seeded["org_b"]},
        )
        connection.execute(
            text(
                "INSERT INTO assets(id,organization_id,canonical_target,display_name,"
                "criticality,policy_hash) "
                "VALUES(:id,:org,'192.0.2.10','Other','low',:hash)"
            ),
            {"id": other_asset, "org": seeded["org_b"], "hash": "a" * 64},
        )
    for changes in (
        {"asset_id": other_asset},
        {"organization_id": seeded["org_b"], "asset_id": other_asset},
        {"actor_id": seeded["users"]["viewer"], "asset_id": other_asset},
    ):
        columns = (
            "id",
            "organization_id",
            "asset_id",
            "actor_id",
            "membership_id",
            "session_id",
            "asset_version",
            "version",
            "policy_hash",
            "key_hash",
            "fingerprint",
            "expires_at",
        )
        values = {**row, **changes, "id": uuid4(), "key_hash": uuid4().hex * 2}
        with admin_engine.connect() as connection:
            with pytest.raises(DBAPIError) as failed:
                connection.execute(
                    text(
                        f"INSERT INTO evidence_operations({','.join(columns)}) "
                        f"VALUES({','.join(':' + column for column in columns)})"
                    ),
                    values,
                )
            assert failed.value.orig.sqlstate == "23503"
            connection.rollback()
    with admin_engine.connect() as connection:
        with pytest.raises(DBAPIError) as failed:
            connection.execute(
                text(
                    "INSERT INTO evidence_versions(organization_id,asset_id,version,operation_id) "
                    "VALUES(:organization_id,:asset_id,:version,:id)"
                ),
                row,
            )
        assert failed.value.orig.sqlstate == "23503"
        connection.rollback()
    for command in (
        "UPDATE evidence_operations SET fingerprint=repeat('b',64)",
        "UPDATE evidence_operations SET state='reserved'",
        "UPDATE evidence_operations SET state='reclaimed'",
    ):
        with admin_engine.connect() as connection:
            with pytest.raises(DBAPIError) as failed:
                connection.execute(text(command))
            assert failed.value.orig.sqlstate == "23514"
            connection.rollback()


def test_runtime_rls_with_check_and_missing_context(setup, runtime_engine, seeded):
    service, credentials, args, _, _ = setup
    service.submit(credentials, **args)
    with runtime_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM evidence_operations")) == 0
        connection.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(seeded["org_a"])},
        )
        with pytest.raises(DBAPIError) as failed:
            connection.execute(
                text("INSERT INTO evidence_quotas(organization_id) VALUES(:org)"),
                {"org": seeded["org_b"]},
            )
        assert failed.value.orig.sqlstate == "42501"
        connection.rollback()


def test_revoked_permission_during_io_is_rechecked(setup, admin_engine, monkeypatch, seeded):
    service, credentials, args, _, _ = setup
    original = service.storage.write

    def write(prepared):
        result = original(prepared)
        with admin_engine.begin() as connection:
            connection.execute(
                text("UPDATE memberships SET role_id=:role WHERE id=:id"),
                {"role": seeded["roles"]["viewer"], "id": seeded["memberships"]["org_owner"]},
            )
        return result

    monkeypatch.setattr(service.storage, "write", write)
    with pytest.raises(ApplicationError) as denied:
        service.submit(credentials, **args)
    assert denied.value.code == "FORBIDDEN"
    assert snapshot(admin_engine)[2] == (0, 0, 0)


def test_stale_inspection_does_not_clean_referenced_operation(setup, admin_engine, monkeypatch):
    service, credentials, args, maintenance, _ = setup
    receipt = service.submit(credentials, **args)
    row = {**snapshot(admin_engine)[0][0], "state": "prepared", "expired": True}
    monkeypatch.setattr(maintenance, "inspect", lambda: ([row], 0))
    assert maintenance.run(execute=True)["cleaned"] == 0
    assert service.storage.read(receipt)


@pytest.mark.parametrize("after_sync", [False, True])
def test_failed_cleanup_sync_keeps_reservation_until_retry_syncs(
    setup, admin_engine, monkeypatch, after_sync
):
    service, credentials, args, maintenance, root = setup
    with monkeypatch.context() as patch:
        patch.setattr(
            service_module, "emit_event", lambda *a, **kw: (_ for _ in ()).throw(OSError())
        )
        with pytest.raises(OSError):
            service.submit(credentials, **args)
    expire(admin_engine)
    original = service.storage.directory.sync

    def uncertain_sync():
        if after_sync:
            original()
        raise OSError("sync result unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(service.storage.directory, "sync", uncertain_sync)
        for _ in range(2):
            with pytest.raises(OSError):
                maintenance.run(execute=True)
            assert snapshot(admin_engine)[0][0]["state"] == "reclaimed"
            assert snapshot(admin_engine)[1]["reserved_objects"] == 1
            assert snapshot(admin_engine)[1]["reserved_bytes"] == MAX_ENVELOPE_BYTES
            assert not list((root / "storage").glob("*.evidence"))
    assert maintenance.run(execute=True)["cleaned"] == 1
    assert snapshot(admin_engine)[1]["reserved_objects"] == 0
    assert snapshot(admin_engine)[1]["reserved_bytes"] == 0
