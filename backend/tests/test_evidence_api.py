"""Real PostgreSQL, Linux private tmpfs, public API; never connect to targets."""

import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.evidence import access as access_module
from app.evidence import service as service_module
from app.evidence.service import EvidenceService
from app.evidence.storage import LocalEvidenceStorage
from app.main import app
from tests import test_evidence_service as evidence_tests

maintenance_engine = evidence_tests.maintenance_engine
setup = evidence_tests.setup
snapshot = evidence_tests.snapshot


@pytest.fixture
def api(setup, monkeypatch):
    _, credentials, args, _, root = setup
    for name, value in {
        "LAB_EVIDENCE_ROOT": str(root / "storage"),
        "LAB_EVIDENCE_KEY_ROOT": str(root / "keys"),
        "LAB_EVIDENCE_ACTIVE_KEY_ID": "lab-1",
    }.items():
        monkeypatch.setenv(name, value)
    return {
        "path": f"/api/v1/evidence/assets/{args['asset_id']}",
        "headers": {
            get_settings().csrf_header_name: credentials.csrf,
            "Origin": "https://testserver",
            "Idempotency-Key": "evidence-1",
            "If-Match": "1",
            "Content-Type": "application/json",
        },
        "raw": args["raw"],
        "root": root,
        "asset_id": args["asset_id"],
    }


def present(client, api, **changes):
    response = client.post(api["path"], content=api["raw"], headers={**api["headers"], **changes})
    return response


def stored(client, api):
    response = present(client, api)
    assert response.status_code == 201, response.text
    return response.json(), api["path"] + "/" + response.json()["id"]


def test_roundtrip_replay_metadata_and_no_plaintext(api, client, admin_engine, monkeypatch):
    def no_target(host, *args, **kwargs):
        assert not str(host).startswith("192.0.2.")
        return original(host, *args, **kwargs)

    original = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", no_target)
    body, path = stored(client, api)
    again = present(client, api)
    assert again.status_code == 201 and again.headers["Idempotency-Replayed"] == "true"
    assert again.json()["id"] == body["id"]
    assert set(body) == {
        "id",
        "asset_id",
        "version",
        "submitted_at",
        "retention_until",
        "content_available",
        "request_id",
    }
    assert client.get(path).json()["id"] == body["id"]
    content = client.get(path + "/content")
    assert content.status_code == 200 and content.headers["Cache-Control"] == "no-store"
    assert content.json()["lab_asset_reference"] == "LAB-123456"
    page = client.get(api["path"]).json()
    assert len(page["items"]) == 1 and page["next_version"] is None
    assert client.get(api["path"] + "/summary").json()["evidence_count"] == 1
    assert snapshot(admin_engine)[2] == (1, 1, 1)
    with admin_engine.connect() as db:
        replay = db.execute(
            text(
                "SELECT response_body FROM http_idempotency_records "
                "WHERE route LIKE '/api/v1/evidence/%'"
            )
        ).scalar_one()
        assert replay == {"operation_id": body["id"]}
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM security_audit_events "
                    "WHERE action='evidence.content_read'"
                )
            )
            == 1
        )
        assert db.scalar(text("SELECT ownership_status FROM assets")) == "unverified"
    for file in (api["root"] / "storage").iterdir():
        assert b"LAB-123456" not in file.read_bytes()


def test_real_image_layout_supports_api_storage(api, client):
    from pathlib import Path

    from app.api.v1 import evidence
    from app.evidence.layout import IMAGE_ROOT_FILE, protected_root

    assert IMAGE_ROOT_FILE.read_text().strip() == str(protected_root(Path(evidence.__file__)))
    assert present(client, api).status_code == 201


@pytest.mark.parametrize(
    "role,write,read",
    [
        ("org_owner", True, True),
        ("security_manager", True, True),
        ("analyst", True, False),
        ("auditor", False, True),
        ("viewer", False, False),
        ("platform_admin", False, False),
    ],
)
def test_role_matrix(api, client, login, role, write, read):
    _, path = stored(client, api)
    auth = login(role)
    api["headers"].update(auth["headers"])
    assert client.get(api["path"] + "/summary").status_code == 200
    for suffix in ("", "/content"):
        expected = 200 if read else 404 if role == "analyst" else 403
        assert client.get(path + suffix).status_code == expected
    page = client.get(api["path"])
    assert page.status_code == (200 if read or role == "analyst" else 403)
    if role == "analyst":
        assert page.json()["items"] == []
    response = present(client, api, **{"Idempotency-Key": "role-write"})
    assert response.status_code == (201 if write else 403), response.text
    if role == "analyst":
        own = api["path"] + "/" + response.json()["id"]
        assert client.get(own + "/content").status_code == 200
        assert len(client.get(api["path"]).json()["items"]) == 1


def test_platform_admin_never_bypasses_even_with_permissions(api, client, login, admin_engine):
    _, path = stored(client, api)
    with admin_engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO role_permissions(role_id,permission_id) "
                "SELECT r.id,p.id FROM roles r CROSS JOIN permissions p "
                "WHERE r.code='platform_admin' AND p.resource='evidence' "
                "ON CONFLICT DO NOTHING"
            )
        )
    api["headers"].update(login("platform_admin")["headers"])
    assert present(client, api).status_code == 403
    assert client.get(path + "/content").status_code == 403


@pytest.mark.parametrize(
    "case,status",
    [
        ("missing_csrf", 403),
        ("wrong_csrf", 403),
        ("origin", 403),
        ("cookie", 401),
        ("if_match", 422),
        ("stale", 409),
        ("key", 422),
        ("compression", 415),
        ("media", 415),
        ("charset", 415),
        ("large", 413),
        ("extra", 422),
        ("duplicate", 422),
        ("invalid", 422),
        ("future", 422),
        ("old", 422),
        ("bool", 422),
        ("empty", 422),
    ],
)
def test_strict_input_and_auth(api, client, admin_engine, case, status):
    headers = api["headers"]
    if case == "missing_csrf":
        headers.pop(get_settings().csrf_header_name)
    elif case == "wrong_csrf":
        headers[get_settings().csrf_header_name] = "wrong"
    elif case == "origin":
        headers["Origin"] = "https://hostile.example"
    elif case == "cookie":
        client.cookies.clear()
    elif case == "if_match":
        headers.pop("If-Match")
    elif case == "stale":
        headers["If-Match"] = "2"
    elif case == "key":
        headers.pop("Idempotency-Key")
    elif case == "compression":
        headers["Content-Encoding"] = "gzip"
    elif case == "media":
        headers["Content-Type"] = "application/octet-stream"
    elif case == "charset":
        headers["Content-Type"] = "application/json;charset=latin-1"
    elif case == "large":
        api["raw"] = b"x" * 16385
    elif case == "duplicate":
        api["raw"] = b'{"schema_version":1,"schema_version":1}'
    elif case == "invalid":
        api["raw"] = b"not-json"
    elif case == "empty":
        api["raw"] = b""
    else:
        body = json.loads(api["raw"])
        if case == "extra":
            body["ip"] = "192.0.2.11"
        elif case == "bool":
            body["schema_version"] = True
        else:
            body["observed_at"] = (
                datetime.now(UTC) + timedelta(days=2 if case == "future" else -2)
            ).isoformat()
        api["raw"] = json.dumps(body).encode()
    response = present(client, api)
    assert response.status_code == status, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert "LAB-123456" not in response.text
    assert snapshot(admin_engine)[0] == []


def test_stream_limit_stops_before_exhausting_chunks(api, client, admin_engine):
    # TestClient's transport coalesces generators: exercise ASGI receive directly
    # via Request to verify early stop, and also exercise the full HTTP chunked flow.
    import asyncio

    from starlette.requests import Request

    from app.api.v1.evidence import bounded_body
    from app.core.errors import ApplicationError

    consumed = []

    async def receive():
        consumed.append(1)
        assert len(consumed) <= 2
        return {"type": "http.request", "body": b"x" * 9000, "more_body": True}

    request = Request(
        {"type": "http", "headers": [(b"content-type", b"application/json")]}, receive
    )
    with pytest.raises(ApplicationError) as failed:
        asyncio.run(bounded_body(request))
    assert failed.value.status_code == 413 and len(consumed) == 2
    response = client.post(api["path"], headers=api["headers"], content=iter([b"x" * 9000] * 3))
    assert response.status_code == 413
    response = client.post(
        api["path"], headers=api["headers"], content=iter([api["raw"][:70], api["raw"][70:]])
    )
    assert response.status_code == 201
    assert snapshot(admin_engine)[2] == (1, 1, 1)


@pytest.mark.parametrize("suffix", ["", "/summary", "/{id}", "/{id}/content"])
def test_other_tenant_is_404(api, client, login, seeded, admin_engine, suffix):
    body, _ = stored(client, api)
    with admin_engine.begin() as db:
        db.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(seeded["org_b"])},
        )
        db.execute(
            text(
                "UPDATE sessions SET organization_id=:org,membership_id=:member WHERE user_id=:user"
            ),
            {
                "org": seeded["org_b"],
                "member": seeded["owner_b_membership"],
                "user": seeded["users"]["org_owner"],
            },
        )
        db.execute(
            text("UPDATE memberships SET role_id=:role WHERE id=:id"),
            {"role": seeded["roles"]["org_owner"], "id": seeded["owner_b_membership"]},
        )
    assert client.get(api["path"] + suffix.format(id=body["id"])).status_code == 404
    assert present(client, api).status_code == 404


@pytest.mark.parametrize("kind", ["session", "membership", "permission", "tenant"])
@pytest.mark.parametrize("operation", ["read", "write"])
def test_authority_revoked_during_io(
    api, client, admin_engine, seeded, monkeypatch, kind, operation
):
    if operation == "read":
        _, path = stored(client, api)
    original = getattr(LocalEvidenceStorage, operation)

    def io(storage, value):
        result = original(storage, value)
        with admin_engine.begin() as db:
            if kind == "session":
                db.execute(text("UPDATE sessions SET revoked_at=now()"))
            elif kind == "membership":
                db.execute(
                    text("UPDATE memberships SET status='suspended' WHERE id=:id"),
                    {"id": seeded["memberships"]["org_owner"]},
                )
            elif kind == "permission":
                db.execute(
                    text(
                        "DELETE FROM role_permissions WHERE permission_id IN "
                        "(SELECT id FROM permissions WHERE resource='evidence')"
                    )
                )
            else:
                db.execute(
                    text("SELECT set_config('app.organization_id',:org,true)"),
                    {"org": str(seeded["org_b"])},
                )
                db.execute(
                    text(
                        "UPDATE sessions SET organization_id=:org,membership_id=:member "
                        "WHERE user_id=:user"
                    ),
                    {
                        "org": seeded["org_b"],
                        "member": seeded["owner_b_membership"],
                        "user": seeded["users"]["org_owner"],
                    },
                )
        return result

    monkeypatch.setattr(LocalEvidenceStorage, operation, io)
    response = client.get(path + "/content") if operation == "read" else present(client, api)
    assert response.status_code in (401, 403, 404), response.text
    assert "LAB-123456" not in response.text
    assert snapshot(admin_engine)[2] == ((1, 1, 1) if operation == "read" else (0, 0, 0))


@pytest.mark.parametrize("fault", ["corrupt", "key", "storage", "audit"])
def test_read_failure_never_delivers_plaintext(
    api, client, admin_engine, monkeypatch, caplog, fault
):
    _, path = stored(client, api)
    if fault == "corrupt":
        next((api["root"] / "storage").glob("*.evidence")).write_bytes(b"damaged")
    elif fault == "key":
        (api["root"] / "keys" / "lab-1.kek").unlink()
    elif fault == "storage":
        monkeypatch.setenv("LAB_EVIDENCE_ROOT", "/missing-private-evidence")
    else:

        def broken(*args, **kwargs):
            raise RuntimeError("LAB-123456 secret path")

        monkeypatch.setattr(access_module, "record_event", broken)
    response = client.get(path + "/content")
    assert response.status_code == 503 and response.headers["Cache-Control"] == "no-store"
    assert "LAB-123456" not in response.text + caplog.text
    with admin_engine.connect() as db:
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM security_audit_events "
                    "WHERE action='evidence.content_read'"
                )
            )
            == 0
        )


def test_policy_archive_retention_and_pagination(api, client, admin_engine, monkeypatch):
    _, path = stored(client, api)
    assert present(client, api, **{"Idempotency-Key": "second"}).json()["version"] == 2
    page = client.get(api["path"], params={"limit": 1}).json()
    assert page["next_version"] == 1
    assert client.get(api["path"], params={"after_version": 1}).json()["items"][0]["version"] == 2
    monkeypatch.delenv("LAB_ASSET_POLICY_JSON")
    assert present(client, api).status_code == 403
    assert client.get(path + "/content").status_code == 200
    with admin_engine.begin() as db:
        db.execute(
            text(
                "UPDATE assets SET deleted_at=now(),archive_reason='Test archive',version=version+1"
            )
        )
    assert present(client, api).status_code == 409
    assert client.get(path + "/content").status_code == 200
    with admin_engine.begin() as db:
        db.execute(text("UPDATE evidence_versions SET created_at=now()-interval '91 days'"))
    assert client.get(path).json()["content_available"] is False
    assert client.get(path + "/content").status_code == 410
    assert client.get(api["path"] + "/summary").json()["evidence_count"] == 2


def test_replay_conflict_expiry_and_old_observation(api, client, admin_engine):
    stored(client, api)
    original = api["raw"]
    api["raw"] = original.replace(b"LAB-123456", b"LAB-123")
    assert present(client, api).status_code == 409
    api["raw"] = original
    with admin_engine.begin() as db:
        db.execute(
            text(
                "UPDATE http_idempotency_records SET created_at=now()-interval '25 hours', "
                "expires_at=now()-interval '1 hour' WHERE route LIKE '/api/v1/evidence/%'"
            )
        )
    assert present(client, api).json()["version"] == 2
    assert snapshot(admin_engine)[2] == (2, 2, 2)


def test_replay_does_not_reapply_submission_freshness(api, client, monkeypatch):
    stored(client, api)

    def no_new_submission(*args, **kwargs):
        raise AssertionError("Replay must not reserve or recheck observation age")

    monkeypatch.setattr(service_module, "parse_submission", no_new_submission)
    assert present(client, api).status_code == 201


@pytest.mark.parametrize(
    "name,value,status",
    [
        ("Content-Length", "-1", 422),
        ("Content-Length", "999999999999", 413),
        ("Content-Length", "word", 422),
        ("Content-Type", "application/json", 422),
    ],
)
def test_unexpected_headers(api, client, name, value, status):
    headers = [*api["headers"].items(), (name, value)]
    assert client.post(api["path"], content=api["raw"], headers=headers).status_code == status


@pytest.mark.parametrize("after_commit", [False, True])
def test_read_audit_commit_failure_is_closed(api, client, admin_engine, monkeypatch, after_commit):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    _, path = stored(client, api)
    original = access_module.record_event

    def mark(session, **kwargs):
        result = original(session, **kwargs)
        session.info["read_audit"] = True
        return result

    def fail_commit(session):
        if session.info.get("read_audit"):
            raise OSError("lost audit commit response")

    monkeypatch.setattr(access_module, "record_event", mark)
    hook = "after_commit" if after_commit else "before_commit"
    event.listen(Session, hook, fail_commit)
    try:
        response = client.get(path + "/content")
        assert response.status_code == 503 and "LAB-123456" not in response.text
    finally:
        event.remove(Session, hook, fail_commit)
    with admin_engine.connect() as db:
        assert db.scalar(
            text("SELECT count(*) FROM security_audit_events WHERE action='evidence.content_read'")
        ) == int(after_commit)


def test_no_db_transaction_during_storage_or_crypto(api, client, admin_engine, monkeypatch):
    calls = []

    def wrap(name, original):
        def checked(storage, *args):
            with admin_engine.connect() as db:
                assert (
                    db.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE "
                            "usename='sentinelai_runtime' AND state='idle in transaction'"
                        )
                    )
                    == 0
                )
            calls.append(name)
            return original(storage, *args)

        return checked

    for name in ("prepare", "write", "read"):
        monkeypatch.setattr(
            LocalEvidenceStorage, name, wrap(name, getattr(LocalEvidenceStorage, name))
        )
    _, path = stored(client, api)
    assert client.get(path + "/content").status_code == 200
    assert calls == ["prepare", "write", "read"]


@pytest.mark.parametrize(
    "phase,after",
    [
        ("reserve", False),
        ("reserve", True),
        ("prepare", False),
        ("prepare", True),
        ("confirm", False),
        ("confirm", True),
    ],
)
def test_http_uncertain_commit_and_retry(api, client, admin_engine, monkeypatch, phase, after):
    original = EvidenceService.commit

    def fail_commit(session, current):
        if current != phase or after:
            original(session, current)
        if current == phase:
            raise OSError("lost response")

    with monkeypatch.context() as patch:
        patch.setattr(EvidenceService, "commit", staticmethod(fail_commit))
        assert present(client, api).status_code == 503
    response = present(client, api)
    expected_success = (phase == "reserve" and not after) or (phase == "confirm" and after)
    assert response.status_code == (201 if expected_success else 409), response.text
    assert snapshot(admin_engine)[2] == ((1, 1, 1) if expected_success else (0, 0, 0))


def test_concurrent_replay_and_no_db_locks_during_io(api, client, admin_engine, monkeypatch):
    entered, release = Event(), Event()
    original = LocalEvidenceStorage.write

    def writing(storage, prepared):
        with admin_engine.connect() as db:
            assert (
                db.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE "
                        "usename='sentinelai_runtime' AND state='idle in transaction'"
                    )
                )
                == 0
            )
        entered.set()
        assert release.wait(10)
        return original(storage, prepared)

    monkeypatch.setattr(LocalEvidenceStorage, "write", writing)
    with TestClient(app, base_url="https://testserver") as other:
        other.cookies.update(client.cookies)
        with ThreadPoolExecutor() as pool:
            first = pool.submit(present, client, api)
            assert entered.wait(10)
            try:
                assert present(other, api).status_code == 409
            finally:
                release.set()
            assert first.result().status_code == 201
        assert present(other, api).status_code == 201
    assert snapshot(admin_engine)[2] == (1, 1, 1)
