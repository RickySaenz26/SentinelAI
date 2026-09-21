"""Deterministic login races exercise PostgreSQL state changes while awaiting a lock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1 import session as sessions_api
from app.platform.crypto import hash_password
from app.platform.database.models import (
    Membership,
    OutboxEvent,
    PasswordCredential,
    SecurityAuditEvent,
    User,
    UserSession,
)


@pytest.mark.parametrize(
    "change",
    [
        "user_suspended",
        "user_deleted",
        "membership_suspended",
        "membership_deleted",
        "password_changed",
        "password_rehashed",
        "credential_deleted",
    ],
)
def test_login_revalidates_after_waiting_for_organization_lock(
    change, monkeypatch, seeded, client, admin_engine
):
    reached_lock, may_continue = Event(), Event()
    original_lock = sessions_api.lock_organization

    def paused_lock(session, organization_id):
        reached_lock.set()
        assert may_continue.wait(timeout=10), "Coordinating transaction did not complete"
        original_lock(session, organization_id)

    monkeypatch.setattr(sessions_api, "lock_organization", paused_lock)

    def authenticate():
        return client.post(
            "/api/v1/session",
            json={"email": seeded["emails"]["viewer"], "password": seeded["password"]},
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(authenticate)
        try:
            assert reached_lock.wait(timeout=10), "Login did not reach organization lock"
            with Session(admin_engine) as session:
                user_id = seeded["users"]["viewer"]
                if change.startswith("user_"):
                    user = session.get(User, user_id)
                    if change == "user_suspended":
                        user.status = "suspended"
                    else:
                        user.deleted_at = datetime.now(UTC)
                elif change.startswith("membership_"):
                    member = session.get(Membership, seeded["memberships"]["viewer"])
                    if change == "membership_suspended":
                        member.status = "suspended"
                    else:
                        member.deleted_at = datetime.now(UTC)
                else:
                    credential = session.get(PasswordCredential, user_id)
                    if change == "credential_deleted":
                        session.delete(credential)
                    else:
                        password = (
                            seeded["password"]
                            if change == "password_rehashed"
                            else "Recovered password changed 42!"
                        )
                        credential.password_hash = hash_password(password)
                session.commit()
        finally:
            may_continue.set()
        response = pending.result(timeout=10)
    expected_count = 1 if change == "password_rehashed" else 0
    if change == "password_rehashed":
        assert response.status_code == 200, response.text
        assert "__Host-sentinel_session" in client.cookies
    else:
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert "__Host-sentinel_session" not in client.cookies
    with Session(admin_engine) as session:
        for model in (UserSession, SecurityAuditEvent, OutboxEvent):
            assert session.scalar(select(func.count()).select_from(model)) == expected_count
