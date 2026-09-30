"""Inventory acceptance with real PostgreSQL; synthetic addresses are never contacted."""

import base64
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.assets import service as asset_service
from app.assets.configuration import get_lab_policy
from app.assets.policy import LabPolicy, exact_ipv4
from app.main import app
from app.platform.database.session import set_organization_context
from app.security_audit.service import verify_chain
from tests.test_postgres_security import denied

TARGET = "192.0.2.10"
OTHER = "192.0.2.11"
EXCLUDED = "192.0.2.12"
POLICY = {
    "version": 1,
    "allowed_targets": [TARGET, OTHER, EXCLUDED],
    "excluded_targets": [EXCLUDED],
    "max_active_assets_per_tenant": 2,
}
BODY = {"type": "ipv4", "target": TARGET, "display_name": "Synthetic lab", "criticality": "low"}


@pytest.fixture
def policy(monkeypatch):
    monkeypatch.setenv("LAB_ASSET_POLICY_JSON", json.dumps(POLICY))


def create(client, auth, *, key="create-1", body=None):
    return client.post(
        "/api/v1/assets", json=body or BODY, headers={**auth["headers"], "Idempotency-Key": key}
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "{}",
        "null",
        "[]",
        "not json",
        "x" * 350001,
        json.dumps({**POLICY, "allowed_targets": []}),
        json.dumps({**POLICY, "max_active_assets_per_tenant": 0}),
        json.dumps({**POLICY, "extra": True}),
        json.dumps({**POLICY, "allowed_targets": [TARGET, TARGET]}),
        json.dumps({**POLICY, "allowed_targets": ["example.com"]}),
    ],
)
def test_missing_empty_invalid_policy_denies_even_platform_admin(client, login, monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("LAB_ASSET_POLICY_JSON", raising=False)
    else:
        monkeypatch.setenv("LAB_ASSET_POLICY_JSON", raw)
    response = create(client, login("platform_admin"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "LAB_POLICY_DENIED"


@pytest.mark.parametrize(
    "target",
    [
        EXCLUDED,
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
    ],
)
def test_exclusions_and_hard_denials(client, login, monkeypatch, target):
    values = {**POLICY, "allowed_targets": [TARGET, target]}
    monkeypatch.setenv("LAB_ASSET_POLICY_JSON", json.dumps(values))
    response = create(client, login(), body={**BODY, "target": target})
    # A private address needs explicit permission; remove it to prove deny-by-default.
    if target == "10.0.0.1":
        assert response.status_code == 201
        monkeypatch.setenv("LAB_ASSET_POLICY_JSON", json.dumps(POLICY))
        response = create(client, login(), body={**BODY, "target": target})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "target",
    [
        "example.com",
        "https://192.0.2.10",
        "::1",
        "::ffff:192.0.2.10",
        "192.0.2.0/24",
        "192.0.2.10-20",
        "192.000.2.10",
        "0xc000020a",
        "3221225994",
        "192.2.10",
        "192.0.2.10 ",
        " 192.0.2.10",
        "192.0.2.256",
        "192.0.2.10\n",
        3221225994,
    ],
)
def test_bad_targets_are_422(client, login, policy, target):
    assert create(client, login(), body={**BODY, "target": target}).status_code == 422


@pytest.mark.parametrize(
    "field",
    [
        "organization_id",
        "id",
        "ownership_status",
        "approved_by",
        "policy_hash",
        "version",
        "command",
        "flags",
    ],
)
def test_client_authority_rejected(client, login, policy, field):
    assert create(client, login(), body={**BODY, field: "forged"}).status_code == 422


@pytest.mark.parametrize(
    "role,expected",
    [
        ("platform_admin", 201),
        ("org_owner", 201),
        ("security_manager", 201),
        ("analyst", 403),
        ("viewer", 403),
        ("auditor", 403),
    ],
)
def test_role_matrix(client, login, policy, role, expected):
    auth = login(role)
    assert create(client, auth).status_code == expected
    assert client.get("/api/v1/assets").status_code == 200
    if expected == 403:
        headers = {**auth["headers"], "If-Match": "1", "Idempotency-Key": "archive"}
        path = f"/api/v1/assets/{uuid4()}"
        assert client.patch(path, headers=headers, json={"display_name": "no"}).status_code == 403
        assert (
            client.request("DELETE", path, headers=headers, json={"reason": "no"}).status_code
            == 403
        )


def test_asset_lifecycle_audit_replay_and_policy_removal(
    client, login, policy, monkeypatch, db, seeded
):
    auth = login()
    response = create(client, auth)
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["ownership_status"] == "unverified" and asset["version"] == 1
    assert response.headers["idempotency-replayed"] == "false"
    path = f"/api/v1/assets/{asset['id']}"
    repeated = create(client, auth)
    assert repeated.status_code == 201 and repeated.json() == asset
    assert repeated.headers["idempotency-replayed"] == "true"
    assert create(client, auth, key="other-key").status_code == 409
    assert create(client, auth, body={**BODY, "criticality": "high"}).status_code == 409
    assert client.get(path).json()["id"] == asset["id"]
    assert (
        client.patch(
            path, headers={**auth["headers"], "If-Match": "9"}, json={"display_name": "stale"}
        ).status_code
        == 409
    )
    monkeypatch.delenv("LAB_ASSET_POLICY_JSON")
    assert create(client, auth).status_code == 403
    changed = client.patch(
        path,
        headers={**auth["headers"], "If-Match": "1"},
        json={"display_name": "Renamed", "criticality": "high"},
    )
    assert changed.status_code == 200 and changed.json()["version"] == 2
    headers = {**auth["headers"], "If-Match": "2", "Idempotency-Key": "archive-1"}
    archived = client.request("DELETE", path, headers=headers, json={"reason": "Lab removed"})
    assert archived.status_code == 204 and not archived.content
    assert (
        client.request("DELETE", path, headers=headers, json={"reason": "Lab removed"}).headers[
            "idempotency-replayed"
        ]
        == "true"
    )
    assert (
        client.request("DELETE", path, headers=headers, json={"reason": "Different"}).status_code
        == 409
    )
    assert (
        client.patch(
            path, headers={**auth["headers"], "If-Match": "3"}, json={"display_name": "restore"}
        ).status_code
        == 409
    )
    assert client.get("/api/v1/assets").json()["items"] == []
    assert len(client.get("/api/v1/assets?status=archived").json()["items"]) == 1
    assert len(client.get("/api/v1/assets?status=all").json()["items"]) == 1
    assert client.get(path).json()["archive_reason"] == "Lab removed"
    set_organization_context(db, seeded["org_a"])
    assert verify_chain(db, seeded["org_a"]) == (True, None)
    assert (
        db.scalar(text("SELECT count(*) FROM security_audit_events WHERE action LIKE 'asset.%'"))
        == 3
    )
    assert (
        db.scalar(text("SELECT count(*) FROM outbox_events WHERE event_type LIKE 'asset.%'")) == 3
    )
    assert db.scalar(text("SELECT count(*) FROM http_idempotency_records")) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"display_name": None},
        {"target": OTHER},
        {"type": "ipv4"},
        {"organization_id": str(uuid4())},
        {"display_name": "   "},
        {"criticality": "unknown"},
    ],
)
def test_patch_rejects_invalid_or_immutable_fields(client, login, policy, payload):
    auth = login()
    asset = create(client, auth).json()
    response = client.patch(
        f"/api/v1/assets/{asset['id']}", json=payload, headers={**auth["headers"], "If-Match": "1"}
    )
    assert response.status_code == 422


def test_headers_csrf_origin_and_openapi(client, login, policy):
    assert client.get("/api/v1/assets").status_code == 401
    auth = login()
    assert client.post("/api/v1/assets", json=BODY, headers=auth["headers"]).status_code == 422
    assert create(client, auth, key="bad key").status_code == 422
    assert (
        client.post("/api/v1/assets", json=BODY, headers={"Idempotency-Key": "x"}).status_code
        == 403
    )
    for headers in (
        {**auth["headers"], "Origin": "https://evil.example"},
        {**auth["headers"], next(k for k in auth["headers"] if k != "Origin"): "bad"},
    ):
        assert create(client, {"headers": headers}).status_code == 403
    asset = create(client, auth).json()
    path = f"/api/v1/assets/{asset['id']}"
    assert (
        client.patch(path, json={"display_name": "x"}, headers=auth["headers"]).status_code == 422
    )
    assert (
        client.request("DELETE", path, json={"reason": "x"}, headers=auth["headers"]).status_code
        == 422
    )
    spec = client.get("/openapi.json").json()
    post = spec["paths"]["/api/v1/assets"]["post"]
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in post["parameters"])
    assert "201" in post["responses"]
    assert post["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "AssetCreate"
    )
    preflight = client.options(
        "/api/v1/assets",
        headers={
            "Origin": "https://testserver",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key,If-Match",
        },
    )
    assert preflight.status_code == 200


def test_pagination_filters_and_cursor_isolation(client, login, policy, seeded):
    auth = login()
    first = create(client, auth).json()
    second = create(
        client, auth, key="2", body={**BODY, "target": OTHER, "criticality": "high"}
    ).json()
    page = client.get("/api/v1/assets?page[limit]=1").json()
    assert page["items"][0]["id"] == first["id"]
    following = client.get(
        "/api/v1/assets", params={"page[limit]": 1, "page[after]": page["page"]["next_cursor"]}
    ).json()
    assert [i["id"] for i in following["items"]] == [second["id"]]
    assert following["page"]["next_cursor"] is None
    assert (
        len(client.get("/api/v1/assets?criticality=high&type=ipv4&q=Synthetic").json()["items"])
        == 1
    )
    assert client.get("/api/v1/assets?q=%25").json()["items"] == []
    for limit in (0, 101):
        assert client.get("/api/v1/assets", params={"page[limit]": limit}).status_code == 422
    for cursor in (
        "!",
        "e30=",
        base64.b64encode(
            json.dumps([str(seeded["org_b"]), datetime.now(UTC).isoformat(), first["id"]]).encode()
        ).decode(),
        base64.b64encode(
            json.dumps([str(seeded["org_a"]), "2026-01-01", first["id"]]).encode()
        ).decode(),
    ):
        assert client.get("/api/v1/assets", params={"page[after]": cursor}).status_code == 422


def test_two_tenants_never_share_resources_or_replay(client, login, policy, seeded, create_user):
    auth = login()
    asset = create(client, auth).json()
    outsider = create_user(role="org_owner", organization_id=seeded["org_b"])
    with TestClient(app, base_url="https://testserver") as other:
        response = other.post(
            "/api/v1/session", json={"email": outsider["email"], "password": outsider["password"]}
        )
        other_auth = {
            "headers": {
                next(k for k in auth["headers"] if k != "Origin"): response.json()["csrf_token"],
                "Origin": "https://testserver",
            }
        }
        assert other.get("/api/v1/assets").json()["items"] == []
        path = f"/api/v1/assets/{asset['id']}"
        assert other.get(path).status_code == 404
        headers = {**other_auth["headers"], "If-Match": "1", "Idempotency-Key": "create-1"}
        assert (
            other.patch(path, headers=headers, json={"display_name": "forged"}).status_code == 404
        )
        assert (
            other.request("DELETE", path, headers=headers, json={"reason": "forged"}).status_code
            == 404
        )
        created = create(other, other_auth)
        assert created.status_code == 201 and created.json()["id"] != asset["id"]


def test_quota_and_expired_key_reuse(client, login, policy, admin_engine, monkeypatch):
    auth = login()
    asset = create(client, auth).json()
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE http_idempotency_records SET created_at=now()-interval '2 days', "
                "expires_at=now()-interval '1 day'"
            )
        )
    changed = create(client, auth, body={**BODY, "target": OTHER})
    assert changed.status_code == 201 and changed.json()["id"] != asset["id"]
    monkeypatch.setenv("LAB_ASSET_POLICY_JSON", json.dumps({**POLICY, "excluded_targets": []}))
    assert (
        create(client, auth, key="limit", body={**BODY, "target": EXCLUDED}).json()["error"]["code"]
        == "ASSET_LIMIT_REACHED"
    )


def test_mutation_rollback_including_audit_outbox_and_replay(
    client, login, policy, monkeypatch, admin_engine
):
    auth = login()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic outbox failure")

    with monkeypatch.context() as patch:
        patch.setattr(asset_service, "emit_event", fail)
        with pytest.raises(RuntimeError, match="synthetic outbox failure"):
            create(client, auth)
    with admin_engine.connect() as connection:
        for table in ("assets", "http_idempotency_records"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert (
            connection.scalar(
                text("SELECT count(*) FROM security_audit_events WHERE action LIKE 'asset.%'")
            )
            == 0
        )
    assert create(client, auth).status_code == 201


def test_concurrent_duplicate_create_and_if_match(client, login, policy):
    auth = login()

    def post():
        with TestClient(app, base_url="https://testserver") as concurrent:
            concurrent.cookies.set("__Host-sentinel_session", auth["token"])
            return create(concurrent, auth)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(), range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert sorted(r.headers["idempotency-replayed"] for r in responses) == ["false", "true"]
    path = f"/api/v1/assets/{responses[0].json()['id']}"

    def patch(name):
        with TestClient(app, base_url="https://testserver") as concurrent:
            concurrent.cookies.set("__Host-sentinel_session", auth["token"])
            return concurrent.patch(
                path, headers={**auth["headers"], "If-Match": "1"}, json={"display_name": name}
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(r.status_code for r in pool.map(patch, ["A", "B"])) == [200, 409]


def test_no_target_network_access(client, login, policy, monkeypatch):
    auth = login()
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("Asset routes must never resolve or connect to a target")

    # PostgreSQL uses psycopg/libpq (not these Python socket APIs).
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    asset = create(client, auth).json()
    path = f"/api/v1/assets/{asset['id']}"
    assert client.get(path).status_code == 200
    assert client.get("/api/v1/assets").status_code == 200
    assert (
        client.patch(
            path, headers={**auth["headers"], "If-Match": "1"}, json={"criticality": "high"}
        ).status_code
        == 200
    )
    assert (
        client.request(
            "DELETE",
            path,
            headers={**auth["headers"], "If-Match": "2", "Idempotency-Key": "archive"},
            json={"reason": "done"},
        ).status_code
        == 204
    )
    assert attempts == []


def test_rls_grants_immutable_ip_and_composite_fk(client, login, policy, db, seeded):
    asset = create(client, login()).json()
    assert db.scalar(text("SELECT count(*) FROM assets")) == 0
    assert db.scalar(text("SELECT count(*) FROM http_idempotency_records")) == 0
    set_organization_context(db, seeded["org_b"])
    assert db.scalar(text("SELECT count(*) FROM assets")) == 0
    assert (
        db.execute(
            text("UPDATE assets SET display_name='other' WHERE id=:id"), {"id": UUID(asset["id"])}
        ).rowcount
        == 0
    )
    set_organization_context(db, seeded["org_a"])
    assert db.scalar(text("SELECT count(*) FROM assets")) == 1
    for table in ("assets", "http_idempotency_records"):
        assert db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:table"),
            {"table": table},
        ).one() == (True, True)
        denied(db, f"DELETE FROM {table}")
    for field in ("canonical_target", "organization_id", "policy_hash", "ownership_status"):
        denied(db, f"UPDATE assets SET {field}={field}")
    denied(db, "UPDATE assets SET display_name='no version'", state="23514")
    set_organization_context(db, seeded["org_b"])
    denied(
        db,
        "INSERT INTO http_idempotency_records (organization_id,actor_id,method,route,key_hash,"
        "fingerprint,asset_id,status_code,response_body,created_at,expires_at) "
        "VALUES (:org,:actor,'POST','/api/v1/assets',:hash,:hash,:asset,201,'{}',"
        "now(),now()+interval '1 day')",
        {
            "org": seeded["org_b"],
            "actor": seeded["users"]["org_owner"],
            "hash": "a" * 64,
            "asset": UUID(asset["id"]),
        },
        state="23503",
    )


def test_policy_parser_is_pure_and_exact(policy):
    parsed = get_lab_policy()
    assert isinstance(parsed, LabPolicy) and parsed.permits(TARGET)
    assert len(parsed.fingerprint) == 64
    assert exact_ipv4(TARGET) == TARGET


@pytest.mark.parametrize(
    "raw",
    [
        '{"version":1,"version":1}',
        json.dumps({**POLICY, "version": True}),
        json.dumps({**POLICY, "version": 1.0}),
        json.dumps({**POLICY, "max_active_assets_per_tenant": True}),
    ],
)
def test_policy_ambiguous_json_fails_closed(monkeypatch, raw):
    monkeypatch.setenv("LAB_ASSET_POLICY_JSON", raw)
    assert get_lab_policy() is None


@pytest.mark.parametrize("action", ["update", "archive"])
def test_rollback_does_not_partially_change_existing_asset(
    client,
    login,
    policy,
    monkeypatch,
    action,
    admin_engine,
):
    auth = login()
    asset = create(client, auth).json()
    path = f"/api/v1/assets/{asset['id']}"

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure after asset flush")

    with monkeypatch.context() as patch:
        patch.setattr(asset_service, "emit_event", fail)
        with pytest.raises(RuntimeError, match="synthetic failure after asset flush"):
            if action == "update":
                client.patch(
                    path,
                    json={"display_name": "rollback"},
                    headers={**auth["headers"], "If-Match": "1"},
                )
            else:
                client.request(
                    "DELETE",
                    path,
                    json={"reason": "rollback"},
                    headers={**auth["headers"], "If-Match": "1", "Idempotency-Key": "del"},
                )
    current = client.get(path).json()
    assert current["version"] == 1 and current["archived_at"] is None
    assert current["display_name"] == asset["display_name"]
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM security_audit_events WHERE action LIKE 'asset.%'")
            )
            == 1
        )
        assert connection.scalar(text("SELECT count(*) FROM http_idempotency_records")) == 1


def test_permission_and_session_revalidated_before_replay(
    client, login, policy, admin_engine, seeded
):
    auth = login("security_manager")
    assert create(client, auth).status_code == 201
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET role_id=:role WHERE id=:id"),
            {
                "role": seeded["roles"]["viewer"],
                "id": seeded["memberships"]["security_manager"],
            },
        )
    assert create(client, auth).status_code == 403
    client.delete("/api/v1/session", headers=auth["headers"])
    assert create(client, auth).status_code == 401


def test_actor_scoped_keys_and_recreate_after_archive(client, login, policy):
    owner = login()
    first = create(client, owner).json()
    manager = login("security_manager")
    assert create(client, manager).status_code == 409
    path = f"/api/v1/assets/{first['id']}"
    assert (
        client.request(
            "DELETE",
            path,
            json={"reason": "replacement"},
            headers={**manager["headers"], "If-Match": "1", "Idempotency-Key": "del"},
        ).status_code
        == 204
    )
    replacement = create(client, manager)
    assert replacement.status_code == 201 and replacement.json()["id"] != first["id"]


def test_concurrent_quota_and_archive(client, login, policy, monkeypatch):
    auth = login()
    monkeypatch.setenv(
        "LAB_ASSET_POLICY_JSON", json.dumps({**POLICY, "max_active_assets_per_tenant": 1})
    )

    def post(target):
        with TestClient(app, base_url="https://testserver") as concurrent:
            concurrent.cookies.set("__Host-sentinel_session", auth["token"])
            return create(concurrent, auth, key=target, body={**BODY, "target": target})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(post, [TARGET, OTHER]))
    assert sorted(r.status_code for r in results) == [201, 409]
    asset_id = next(r.json()["id"] for r in results if r.status_code == 201)

    def archive(key):
        with TestClient(app, base_url="https://testserver") as concurrent:
            concurrent.cookies.set("__Host-sentinel_session", auth["token"])
            return concurrent.request(
                "DELETE",
                f"/api/v1/assets/{asset_id}",
                json={"reason": "done"},
                headers={**auth["headers"], "If-Match": "1", "Idempotency-Key": key},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(r.status_code for r in pool.map(archive, ["a", "b"])) == [204, 409]


@pytest.mark.parametrize(
    "table,allowed",
    [
        (
            "assets",
            {
                "display_name",
                "criticality",
                "version",
                "updated_at",
                "deleted_at",
                "archive_reason",
            },
        ),
        (
            "http_idempotency_records",
            {"fingerprint", "asset_id", "status_code", "response_body", "created_at", "expires_at"},
        ),
    ],
)
def test_asset_grants_exact(db, table, allowed):
    assert not db.scalar(
        text("SELECT has_table_privilege(current_user,:table,'UPDATE')"), {"table": table}
    )
    rows = db.execute(
        text(
            "SELECT attname,has_column_privilege(current_user,:table,attname,'UPDATE') "
            "FROM pg_attribute WHERE attrelid=CAST(:table AS regclass) "
            "AND attnum>0 AND NOT attisdropped"
        ),
        {"table": table},
    ).all()
    assert {name for name, permitted in rows if permitted} == allowed


def test_cross_tenant_insert_and_archived_sql_update_denied(client, login, policy, db, seeded):
    auth = login()
    asset = create(client, auth).json()
    client.request(
        "DELETE",
        f"/api/v1/assets/{asset['id']}",
        json={"reason": "done"},
        headers={**auth["headers"], "If-Match": "1", "Idempotency-Key": "del"},
    )
    set_organization_context(db, seeded["org_a"])
    denied(db, "UPDATE assets SET version=version+1,display_name='change archived'", state="23514")
    denied(
        db,
        "INSERT INTO assets(organization_id,canonical_target,display_name,criticality,policy_hash) "
        "VALUES(:org,'192.0.2.11','Foreign','low',:hash)",
        {"org": seeded["org_b"], "hash": "a" * 64},
    )


@pytest.mark.parametrize("identifier", [17, {}, [], True])
def test_cursor_invalid_identifier_type_is_422(client, login, seeded, identifier):
    login()
    cursor = base64.urlsafe_b64encode(
        json.dumps([str(seeded["org_a"]), datetime.now(UTC).isoformat(), identifier]).encode()
    ).decode()
    response = client.get("/api/v1/assets", params={"page[after]": cursor})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CURSOR"
