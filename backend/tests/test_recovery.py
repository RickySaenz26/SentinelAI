"""Recovery uses opaque, single-use tokens and an anonymous recovery actor."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.orm import Session

from app.api.v1 import account_recovery
from app.identity import recovery_delivery
from app.identity.recovery_delivery import clear_test_messages, pop_test_message
from app.main import app
from app.platform.crypto import generate_token, hash_token, verify_password
from app.platform.database.models import (
    AccountRecoveryToken,
    Membership,
    Organization,
    OutboxEvent,
    PasswordCredential,
    SecurityAuditEvent,
    User,
    UserSession,
)
from app.platform.database.session import set_organization_context

NEW_PASSWORD = "Recovered secret password 99!"


@pytest.fixture(autouse=True)
def clear_messages():
    clear_test_messages()
    yield
    clear_test_messages()


def request_token(client, email):
    response = client.post("/api/v1/account-recovery", json={"email": email})
    assert response.status_code == 202
    delivered_email, raw = pop_test_message()
    assert delivered_email == email
    return raw


def seed_token(admin_engine, user_id, *, expired=False):
    raw = generate_token()
    with Session(admin_engine) as session:
        session.add(
            AccountRecoveryToken(
                user_id=user_id,
                token_hash=hash_token(raw),
                expires_at=datetime.now(UTC) + timedelta(minutes=-1 if expired else 30),
            )
        )
        session.commit()
    return raw


def confirm(client, token):
    return client.post(
        "/api/v1/account-recovery/confirm",
        json={
            "token": token,
            "new_password": NEW_PASSWORD,
        },
    )


def test_recovery_request_is_non_enumerative_and_anonymous(client, seeded, db):
    known = client.post("/api/v1/account-recovery", json={"email": seeded["emails"]["viewer"]})
    unknown = client.post("/api/v1/account-recovery", json={"email": "missing@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content == b""
    _, raw = pop_test_message()
    stored = db.scalar(select(AccountRecoveryToken))
    assert stored.token_hash == hash_token(raw) and stored.token_hash != raw
    set_organization_context(db, seeded["org_a"])
    event = db.scalar(select(SecurityAuditEvent))
    assert event.actor_type == "recovery" and event.actor_user_id is None
    assert event.resource_id == seeded["users"]["viewer"]
    outbox = db.scalar(select(OutboxEvent))
    assert outbox.idempotency_key == f"recovery.requested:{stored.id}"
    assert raw not in str(event.details) + str(outbox.payload)


@pytest.mark.parametrize("inactive", ["user", "deleted_user", "organization", "membership"])
def test_inactive_accounts_receive_same_generic_response(client, seeded, admin_engine, inactive):
    model, identity, changes = {
        "user": (User, seeded["users"]["viewer"], {"status": "suspended"}),
        "deleted_user": (User, seeded["users"]["viewer"], {"deleted_at": datetime.now(UTC)}),
        "organization": (Organization, seeded["org_a"], {"status": "suspended"}),
        "membership": (Membership, seeded["memberships"]["viewer"], {"status": "suspended"}),
    }[inactive]
    with admin_engine.begin() as connection:
        connection.execute(update(model).where(model.id == identity).values(**changes))
    response = client.post("/api/v1/account-recovery", json={"email": seeded["emails"]["viewer"]})
    assert response.status_code == 202 and response.content == b""
    with pytest.raises(RuntimeError, match="No recovery"):
        pop_test_message()


def test_recovery_consumes_once_and_revokes_all_sessions(login, seeded, db):
    auth = login("viewer")
    token = request_token(auth["client"], seeded["emails"]["viewer"])
    assert confirm(auth["client"], token).status_code == 204
    assert confirm(auth["client"], token).status_code == 400
    assert auth["client"].get("/api/v1/me").status_code == 401
    credential = db.get(PasswordCredential, seeded["users"]["viewer"])
    assert verify_password(credential.password_hash, NEW_PASSWORD)[0]
    sessions = db.scalars(
        select(UserSession).where(UserSession.user_id == seeded["users"]["viewer"])
    ).all()
    assert sessions and all(item.revoked_at for item in sessions)
    token_row = db.scalar(select(AccountRecoveryToken))
    assert token_row.consumed_at is not None
    set_organization_context(db, seeded["org_a"])
    completed = db.scalars(
        select(SecurityAuditEvent).where(SecurityAuditEvent.action == "account_recovery.completed")
    ).all()
    assert len(completed) == 1 and completed[0].actor_user_id is None


@pytest.mark.parametrize(
    "invalid", ["unknown", "expired", "user", "deleted_user", "membership", "organization"]
)
def test_invalid_recovery_cannot_change_credentials(client, seeded, admin_engine, db, invalid):
    user_id = seeded["users"]["viewer"]
    raw = seed_token(admin_engine, user_id, expired=invalid == "expired")
    if invalid == "unknown":
        raw = generate_token()
    if invalid in ("user", "deleted_user", "membership", "organization"):
        model, identity = {
            "user": (User, user_id),
            "deleted_user": (User, user_id),
            "membership": (Membership, seeded["memberships"]["viewer"]),
            "organization": (Organization, seeded["org_a"]),
        }[invalid]
        changes = (
            {"deleted_at": datetime.now(UTC)}
            if invalid == "deleted_user"
            else {"status": "suspended"}
        )
        with admin_engine.begin() as connection:
            connection.execute(update(model).where(model.id == identity).values(**changes))
    response = confirm(client, raw)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RECOVERY_TOKEN_INVALID"
    assert verify_password(db.get(PasswordCredential, user_id).password_hash, seeded["password"])[0]


def test_recovery_can_restore_missing_credential(client, seeded, admin_engine, db):
    user_id = seeded["users"]["viewer"]
    raw = seed_token(admin_engine, user_id)
    with admin_engine.begin() as connection:
        connection.execute(delete(PasswordCredential).where(PasswordCredential.user_id == user_id))
    assert confirm(client, raw).status_code == 204
    assert verify_password(db.get(PasswordCredential, user_id).password_hash, NEW_PASSWORD)[0]


def test_recovery_rechecks_expiry_after_waiting_for_locks(
    client,
    seeded,
    admin_engine,
    db,
    monkeypatch,
):
    user_id = seeded["users"]["viewer"]
    raw = seed_token(admin_engine, user_id)
    start_time = datetime.now(UTC)
    current_time = [start_time]

    class ControlledClock:
        @staticmethod
        def now(_timezone):
            return current_time[0]

    original_lock = account_recovery.lock_recovery_sessions

    def advance_clock(session):
        original_lock(session)
        with admin_engine.begin() as connection:
            connection.execute(
                update(AccountRecoveryToken)
                .where(AccountRecoveryToken.token_hash == hash_token(raw))
                .values(expires_at=start_time + timedelta(seconds=1))
            )
        current_time[0] = start_time + timedelta(seconds=2)

    monkeypatch.setattr(account_recovery, "datetime", ControlledClock)
    monkeypatch.setattr(account_recovery, "lock_recovery_sessions", advance_clock)
    response = confirm(client, raw)
    assert response.status_code == 400
    assert db.scalar(select(AccountRecoveryToken)).consumed_at is None
    assert verify_password(db.get(PasswordCredential, user_id).password_hash, seeded["password"])[0]


def test_concurrent_recovery_has_only_one_winner(client, seeded, db, monkeypatch):
    raw = request_token(client, seeded["emails"]["viewer"])
    barrier = Barrier(2)
    original_lock = account_recovery.lock_recovery_sessions

    def synchronize_lock(session):
        barrier.wait(timeout=20)
        original_lock(session)

    monkeypatch.setattr(account_recovery, "lock_recovery_sessions", synchronize_lock)

    def consume(_index):
        with TestClient(app, base_url="https://testserver") as concurrent_client:
            return confirm(concurrent_client, raw).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume, range(2)))
    assert sorted(results) == [204, 400]
    set_organization_context(db, seeded["org_a"])
    assert (
        db.scalar(
            select(func.count())
            .select_from(SecurityAuditEvent)
            .where(SecurityAuditEvent.action == "account_recovery.completed")
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "account_recovery.completed")
        )
        == 1
    )


def test_recovery_rolls_back_credential_token_sessions_and_audit_on_outbox_failure(
    login,
    seeded,
    db,
    monkeypatch,
):
    auth = login("viewer")
    raw = request_token(auth["client"], seeded["emails"]["viewer"])

    def fail_outbox(*_args, **_kwargs):
        raise RuntimeError("Injected transactional failure")

    monkeypatch.setattr(account_recovery, "emit_event", fail_outbox)
    with pytest.raises(RuntimeError, match="Injected transactional"):
        confirm(auth["client"], raw)
    assert verify_password(
        db.get(PasswordCredential, seeded["users"]["viewer"]).password_hash, seeded["password"]
    )[0]
    assert db.scalar(select(AccountRecoveryToken)).consumed_at is None
    assert db.scalar(select(UserSession)).revoked_at is None
    set_organization_context(db, seeded["org_a"])
    assert (
        db.scalar(
            select(func.count())
            .select_from(SecurityAuditEvent)
            .where(SecurityAuditEvent.action == "account_recovery.completed")
        )
        == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "account_recovery.completed")
        )
        == 0
    )


def test_recovery_and_organization_switch_do_not_deadlock(login, seeded, runtime_engine, db):
    auth = login("org_owner")
    raw = request_token(auth["client"], seeded["emails"]["org_owner"])
    barrier = Barrier(2)

    def before_lock(_connection, _cursor, statement, _parameters, _context, _many):
        if "sentinelai-org-switch" in statement:
            barrier.wait(timeout=20)

    def switch():
        with TestClient(app, base_url="https://testserver") as client:
            return client.post(
                "/api/v1/me/active-organization",
                json={"organization_id": str(seeded["org_b"])},
                headers={**auth["headers"], "Cookie": f"__Host-sentinel_session={auth['token']}"},
            ).status_code

    def recover():
        with TestClient(app, base_url="https://testserver") as client:
            return confirm(client, raw).status_code

    event.listen(runtime_engine, "before_cursor_execute", before_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            switch_future = executor.submit(switch)
            recover_future = executor.submit(recover)
            assert recover_future.result(timeout=25) == 204
            assert switch_future.result(timeout=25) in (200, 401)
    finally:
        event.remove(runtime_engine, "before_cursor_execute", before_lock)
    sessions = db.scalars(
        select(UserSession).where(UserSession.user_id == seeded["users"]["org_owner"])
    ).all()
    assert sessions and all(session.revoked_at is not None for session in sessions)


def test_recovery_request_and_confirm_rate_limits(client, seeded):
    email = seeded["emails"]["viewer"]
    for _ in range(3):
        assert client.post("/api/v1/account-recovery", json={"email": email}).status_code == 202
    assert client.post("/api/v1/account-recovery", json={"email": email}).status_code == 429
    raw = generate_token()
    for _ in range(5):
        assert confirm(client, raw).status_code == 400
    assert confirm(client, raw).status_code == 429


def test_recovery_capture_is_unavailable_outside_test_mode(monkeypatch):
    monkeypatch.setattr(
        recovery_delivery, "get_settings", lambda: SimpleNamespace(environment="local")
    )
    recovery_delivery.deliver_recovery_token("receiver@example.com", generate_token())
    with pytest.raises(RuntimeError, match="only in test mode"):
        pop_test_message()
    monkeypatch.setattr(
        recovery_delivery, "get_settings", lambda: SimpleNamespace(environment="test")
    )
    with pytest.raises(RuntimeError, match="No recovery"):
        pop_test_message()
