"""Managed-user CLI tests use real runtime PostgreSQL and hidden input stubs."""

import runpy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform import create_managed_user as onboarding
from app.platform.crypto import verify_password
from app.platform.database.models import (
    Membership,
    Organization,
    OutboxEvent,
    PasswordCredential,
    Role,
    SecurityAuditEvent,
    User,
)

PASSWORD = "Managed test password 42!"


@pytest.fixture
def hidden_password(monkeypatch):
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return PASSWORD

    monkeypatch.setattr(onboarding.getpass, "getpass", read)
    return prompts


def table_counts(admin_engine):
    with Session(admin_engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (User, Membership, PasswordCredential, SecurityAuditEvent, OutboxEvent)
        )


@pytest.mark.parametrize("role", sorted(onboarding.MANAGED_ROLES))
def test_managed_user_valid_and_repeat_safe(seeded, admin_engine, hidden_password, capsys, role):
    onboarding.create_managed_user("MANAGED@example.com", " Managed User ", seeded["org_a"], role)
    assert len(hidden_password) == 2
    assert not capsys.readouterr().out
    with Session(admin_engine) as session:
        user = session.scalar(select(User).where(User.email == "managed@example.com"))
        assert user.display_name == "Managed User" and user.status == "active"
        credential = session.get(PasswordCredential, user.id)
        assert credential.password_hash != PASSWORD
        assert verify_password(credential.password_hash, PASSWORD)[0]
        member = session.scalar(select(Membership).where(Membership.user_id == user.id))
        assert member.role_id == seeded["roles"][role] and member.organization_id == seeded["org_a"]
        audit = session.scalar(
            select(SecurityAuditEvent).where(SecurityAuditEvent.action == "managed_user.created")
        )
        assert audit.actor_type == "system" and audit.actor_user_id is None
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == user.id))
        assert event.idempotency_key == f"managed_user.created:{user.id}:1"
    before = table_counts(admin_engine)
    with pytest.raises(ValueError, match="already exists"):
        onboarding.create_managed_user("managed@example.com", "Repeat", seeded["org_a"], role)
    assert table_counts(admin_engine) == before


@pytest.mark.parametrize("role", ["platform_admin", "future_global_admin", "", "ORG_OWNER"])
def test_onboarding_closed_allowlist_rejects_before_prompt(monkeypatch, role):
    def forbidden_input(_):
        pytest.fail("Forbidden role must be rejected before password input")

    monkeypatch.setattr(onboarding.getpass, "getpass", forbidden_input)
    with pytest.raises(ValueError, match="not allowed"):
        onboarding.create_managed_user("managed@example.com", "Managed", uuid4(), role)


@pytest.mark.parametrize(
    "email,name,message",
    [
        ("not-an-email", "Name", "Email is not valid"),
        ("managed@example.com", "  ", "Display name"),
    ],
)
def test_onboarding_validates_identity(email, name, message):
    with pytest.raises(ValueError, match=message):
        onboarding.create_managed_user(email, name, uuid4(), "viewer")


@pytest.mark.parametrize(
    "password,confirmation,message",
    [
        (PASSWORD, "Different password", "confirmation"),
        ("short", "short", "between 12 and 1024"),
        ("x" * 1025, "x" * 1025, "between 12 and 1024"),
    ],
)
def test_onboarding_password_validation(monkeypatch, password, confirmation, message):
    values = iter((password, confirmation))
    monkeypatch.setattr(onboarding.getpass, "getpass", lambda _: next(values))
    with pytest.raises(ValueError, match=message):
        onboarding.create_managed_user("managed@example.com", "Name", uuid4(), "viewer")


@pytest.mark.parametrize("state", ["missing", "suspended", "deleted"])
def test_onboarding_requires_active_organization(seeded, admin_engine, hidden_password, state):
    organization_id = seeded["org_a"]
    if state == "missing":
        organization_id = uuid4()
    else:
        with Session(admin_engine) as session:
            org = session.get(Organization, organization_id)
            if state == "suspended":
                org.status = "suspended"
            else:
                org.deleted_at = datetime.now(UTC)
            session.commit()
    before = table_counts(admin_engine)
    with pytest.raises(ValueError, match="Organization is not active"):
        onboarding.create_managed_user("managed@example.com", "Name", organization_id, "viewer")
    assert table_counts(admin_engine) == before


def test_onboarding_requires_available_allowlisted_role(seeded, admin_engine, hidden_password):
    with Session(admin_engine) as session:
        session.get(Role, seeded["roles"]["viewer"]).code = "temporarily_unavailable_viewer"
        session.commit()
    try:
        with pytest.raises(ValueError, match="not available"):
            onboarding.create_managed_user("managed@example.com", "Name", seeded["org_a"], "viewer")
    finally:
        with Session(admin_engine) as session:
            session.get(Role, seeded["roles"]["viewer"]).code = "viewer"
            session.commit()


def test_onboarding_rolls_back_identity_membership_audit_and_outbox(
    monkeypatch, seeded, admin_engine, hidden_password
):
    before = table_counts(admin_engine)

    def fail_outbox(*args, **kwargs):
        raise RuntimeError("Injected outbox failure")

    monkeypatch.setattr(onboarding, "emit_event", fail_outbox)
    with pytest.raises(RuntimeError, match="Injected outbox"):
        onboarding.create_managed_user("managed@example.com", "Name", seeded["org_a"], "viewer")
    assert table_counts(admin_engine) == before


def test_onboarding_concurrent_repetition_has_one_user(seeded, admin_engine, hidden_password):
    barrier = Barrier(2)
    before = table_counts(admin_engine)

    def create(_):
        barrier.wait(timeout=10)
        try:
            onboarding.create_managed_user("managed@example.com", "Name", seeded["org_a"], "viewer")
        except ValueError as error:
            assert "already exists" in str(error)
            return "duplicate"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))
    assert sorted(results) == ["created", "duplicate"]
    assert table_counts(admin_engine) == tuple(count + 1 for count in before)


def test_onboarding_cli_entrypoint_does_not_print_credentials(
    monkeypatch, seeded, admin_engine, hidden_password, capsys
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "managed-user",
            "--email",
            "cli-managed@example.com",
            "--display-name",
            "CLI Managed",
            "--organization-id",
            str(seeded["org_a"]),
            "--role-code",
            "viewer",
        ],
    )
    runpy.run_module("app.platform.create_managed_user", run_name="__main__")
    output = capsys.readouterr().out
    assert output.strip() == "Managed user created. No credentials were printed."
    assert PASSWORD not in output and "cli-managed@example.com" not in output
    with Session(admin_engine) as session:
        assert session.scalar(select(User.id).where(User.email == "cli-managed@example.com"))
