"""Collected HTTP contracts using real runtime PostgreSQL, RLS and CSRF."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.dependencies import SESSION_COOKIE
from app.core.config import get_settings
from app.main import app
from app.platform.crypto import hash_token
from app.platform.database.models import (
    Membership,
    Organization,
    PasswordCredential,
    User,
    UserSession,
)


def test_collected_contract_smoke(client, login, seeded):
    auth = login("platform_admin")
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["platform_admin"]
    organizations = client.get("/api/v1/organizations")
    assert organizations.status_code == 200
    assert [item["id"] for item in organizations.json()["items"]] == [str(seeded["org_a"])]
    roles = client.get("/api/v1/roles")
    assert roles.status_code == 200
    assert len(roles.json()["items"]) == 6
    audit = client.get("/api/v1/security-audit-events")
    assert audit.status_code == 200
    assert any(item["action"] == "session.login" for item in audit.json()["items"])
    conflict = client.patch(
        f"/api/v1/organizations/{seeded['org_a']}",
        json={"name": "Concurrent update"},
        headers={**auth["headers"], "If-Match": "999"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"
    recovery = client.post("/api/v1/account-recovery", json={"email": "unknown@example.com"})
    assert recovery.status_code == 202


def test_login_cookie_and_storage_are_opaque(login, admin_engine):
    auth = login()
    cookie = auth["response"].headers["set-cookie"]
    for flag in ("__Host-sentinel_session=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag in cookie
    assert "Domain=" not in cookie
    assert "." not in auth["token"]
    with Session(admin_engine) as db:
        row = db.scalar(select(UserSession))
        assert row.token_hash == hash_token(auth["token"])
        assert row.csrf_secret_hash == hash_token(auth["csrf"])
        assert row.token_hash != auth["token"]
        assert row.csrf_secret_hash != auth["csrf"]


def test_bad_login_is_non_enumerative(client, seeded):
    responses = [
        client.post("/api/v1/session", json={"email": email, "password": "wrong password"})
        for email in (seeded["emails"]["viewer"], "absent@example.com")
    ]
    assert [r.status_code for r in responses] == [401, 401]
    for response in responses:
        error = response.json()["error"]
        assert error["code"] == "INVALID_CREDENTIALS"
        assert error["message"] == "Credenciales no válidas."
        assert error["details"] == []
        assert SESSION_COOKIE not in response.cookies


@pytest.mark.parametrize("csrf", [None, "incorrect", "old-header"])
def test_missing_wrong_and_literal_default_csrf_rejected(client, login, csrf):
    auth = login()
    headers = {"Origin": "https://testserver"}
    if csrf == "old-header":
        assert get_settings().csrf_header_name != "X-CSRF-Token"
        headers["X-CSRF-Token"] = auth["csrf"]
    elif csrf is not None:
        headers[get_settings().csrf_header_name] = csrf
    response = client.post("/api/v1/session/rotate", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_VALIDATION_FAILED"


def test_untrusted_origin_rejected(client, login):
    auth = login()
    response = client.post(
        "/api/v1/session/rotate", headers={**auth["headers"], "Origin": "https://hostile.example"}
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_custom_csrf_openapi_and_preflight(client):
    header = get_settings().csrf_header_name
    schema = client.get("/openapi.json").json()
    parameters = schema["paths"]["/api/v1/session/rotate"]["post"]["parameters"]
    assert any(p["name"] == header and p["in"] == "header" for p in parameters)
    result = client.options(
        "/api/v1/organizations/ignored",
        headers={
            "Origin": "https://testserver",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": f"{header},If-Match,Content-Type",
        },
    )
    assert result.status_code == 200
    assert result.headers["access-control-allow-origin"] == "https://testserver"
    assert header.lower() in result.headers["access-control-allow-headers"].lower()
    forbidden = client.options(
        "/api/v1/session/rotate",
        headers={"Origin": "https://hostile.example", "Access-Control-Request-Method": "POST"},
    )
    assert forbidden.status_code == 400


def test_rotation_and_logout_invalidate_old_sessions(client, login, admin_engine):
    auth = login()
    rotated = client.post("/api/v1/session/rotate", headers=auth["headers"])
    assert rotated.status_code == 200
    new_token = client.cookies.get(SESSION_COOKIE)
    assert new_token != auth["token"]
    assert rotated.json()["csrf_token"] != auth["csrf"]
    with TestClient(app, base_url="https://testserver") as other:
        other.cookies.set(SESSION_COOKIE, auth["token"])
        assert other.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me").status_code == 200
    result = client.delete(
        "/api/v1/session", headers={get_settings().csrf_header_name: rotated.json()["csrf_token"]}
    )
    assert result.status_code == 204
    assert "Max-Age=0" in result.headers["set-cookie"]
    assert "Secure" in result.headers["set-cookie"]
    assert "HttpOnly" in result.headers["set-cookie"]
    assert client.get("/api/v1/me").status_code == 401
    with Session(admin_engine) as db:
        assert all(row.revoked_at is not None for row in db.scalars(select(UserSession)))


def test_idle_deadline_persisted_and_capped(client, login, admin_engine):
    auth = login()
    now = datetime.now(UTC)
    with admin_engine.begin() as db:
        db.execute(
            update(UserSession).values(
                last_seen_at=now - timedelta(minutes=10),
                idle_expires_at=now + timedelta(minutes=2),
                expires_at=now + timedelta(minutes=3),
            )
        )
    assert client.get("/api/v1/me").status_code == 200
    with Session(admin_engine) as db:
        row = db.scalar(
            select(UserSession).where(UserSession.token_hash == hash_token(auth["token"]))
        )
        assert row.last_seen_at >= now
        assert row.idle_expires_at == row.expires_at


@pytest.mark.parametrize("field", ["idle_expires_at", "expires_at", "revoked_at"])
def test_expired_or_revoked_session_rejected(client, login, admin_engine, field):
    login()
    with admin_engine.begin() as db:
        db.execute(update(UserSession).values({field: datetime.now(UTC) - timedelta(seconds=1)}))
    assert client.get("/api/v1/me").status_code == 401


@pytest.mark.parametrize(
    "model,field",
    [
        (User, "status"),
        (User, "deleted_at"),
        (Membership, "status"),
        (Membership, "deleted_at"),
        (Organization, "status"),
        (Organization, "deleted_at"),
    ],
)
def test_inactive_identity_or_scope_rejects_login_and_session(
    client, login, seeded, admin_engine, model, field
):
    login("viewer")
    target = {
        User: seeded["users"]["viewer"],
        Membership: seeded["memberships"]["viewer"],
        Organization: seeded["org_a"],
    }[model]
    with admin_engine.begin() as db:
        db.execute(
            update(model)
            .where(model.id == target)
            .values({field: "suspended" if field == "status" else datetime.now(UTC)})
        )
    assert client.get("/api/v1/me").status_code == 401
    response = client.post(
        "/api/v1/session",
        json={"email": seeded["emails"]["viewer"], "password": seeded["password"]},
    )
    assert response.status_code == 401


def test_missing_credentials_and_membership_fail_closed(client, seeded, create_user, admin_engine):
    without_membership = create_user(organization_id=seeded["org_a"], role=None)
    with admin_engine.begin() as db:
        db.execute(
            PasswordCredential.__table__.delete().where(
                PasswordCredential.user_id == seeded["users"]["analyst"]
            )
        )
    for email in (without_membership["email"], seeded["emails"]["analyst"]):
        assert (
            client.post(
                "/api/v1/session", json={"email": email, "password": seeded["password"]}
            ).status_code
            == 401
        )


def test_switch_org_rotates_session_and_scope(client, login, seeded):
    auth = login()
    result = client.post(
        "/api/v1/me/active-organization",
        headers=auth["headers"],
        json={"organization_id": str(seeded["org_b"])},
    )
    assert result.status_code == 200
    assert result.json()["active_organization_id"] == str(seeded["org_b"])
    assert client.cookies.get(SESSION_COOKIE) != auth["token"]
    assert client.get("/api/v1/me").json()["roles"] == ["viewer"]
    assert client.get(f"/api/v1/organizations/{seeded['org_a']}").status_code == 404


@pytest.mark.parametrize("target", ["unknown", "suspended", "deleted", "membership"])
def test_switch_invalid_scope_is_not_enumerative(client, login, seeded, admin_engine, target):
    auth = login()
    organization_id = seeded["org_b"]
    with admin_engine.begin() as db:
        if target == "unknown":
            organization_id = uuid4()
        elif target == "membership":
            db.execute(
                update(Membership)
                .where(Membership.id == seeded["owner_b_membership"])
                .values(status="suspended")
            )
        else:
            db.execute(
                update(Organization)
                .where(Organization.id == organization_id)
                .values(
                    {"status": "suspended"}
                    if target == "suspended"
                    else {"deleted_at": datetime.now(UTC)}
                )
            )
    response = client.post(
        "/api/v1/me/active-organization",
        headers=auth["headers"],
        json={"organization_id": str(organization_id)},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ORGANIZATION_NOT_FOUND"


def test_concurrent_opposite_organization_switches(client, login, seeded):
    with TestClient(app, base_url="https://testserver") as second:
        first_auth = login()
        second_auth = login(client=second)
        initial_switch = second.post(
            "/api/v1/me/active-organization",
            headers=second_auth["headers"],
            json={"organization_id": str(seeded["org_b"])},
        )
        assert initial_switch.status_code == 200
        barrier = Barrier(2)

        def switch(active_client, headers, target):
            barrier.wait(timeout=15)
            return active_client.post(
                "/api/v1/me/active-organization",
                headers=headers,
                json={"organization_id": str(target)},
            ).status_code

        with ThreadPoolExecutor(2) as pool:
            jobs = [
                pool.submit(switch, client, first_auth["headers"], seeded["org_b"]),
                pool.submit(
                    switch,
                    second,
                    {get_settings().csrf_header_name: initial_switch.json()["csrf_token"]},
                    seeded["org_a"],
                ),
            ]
            assert [job.result(timeout=20) for job in jobs] == [200, 200]


def test_request_validation_does_not_echo_password(client):
    response = client.post(
        "/api/v1/session", json={"email": "invalid", "password": "sensitive-value"}
    )
    assert response.status_code == 422
    assert "sensitive-value" not in response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_session_expiry_rechecked_after_lock_wait(client, login, monkeypatch):
    from app.api.v1 import dependencies

    login()
    real_now = datetime.now(UTC)
    clock = {"now": real_now}
    original_lock = dependencies.lock_organization

    class Clock:
        @staticmethod
        def now(zone):
            return clock["now"]

    def delayed_lock(db, organization_id):
        original_lock(db, organization_id)
        clock["now"] = real_now + timedelta(days=2)

    monkeypatch.setattr(dependencies, "datetime", Clock)
    monkeypatch.setattr(dependencies, "lock_organization", delayed_lock)
    assert client.get("/api/v1/me").status_code == 401


def test_unknown_account_still_verifies_argon2(client, monkeypatch):
    from app.api.v1 import session as endpoint

    calls = []
    real_verify = endpoint.verify_password

    def observed_verification(encoded, password):
        calls.append(encoded)
        return real_verify(encoded, password)

    monkeypatch.setattr(endpoint, "verify_password", observed_verification)
    response = client.post(
        "/api/v1/session", json={"email": "unknown@example.com", "password": "wrong password"}
    )
    assert response.status_code == 401
    assert len(calls) == 1 and calls[0].startswith("$argon2id$")
