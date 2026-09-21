import runpy
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform import bootstrap as module
from app.platform.database.models import (
    Membership,
    Organization,
    OutboxEvent,
    Role,
    SecurityAuditEvent,
    User,
)

PASSWORD = "Initial test password 42!"


@pytest.fixture
def hidden_password(monkeypatch):
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: PASSWORD)


def test_bootstrap_initial_and_repeat(hidden_password, admin_engine):
    module.bootstrap("ADMIN@example.com", " Admin ", "Initial Organization")
    with Session(admin_engine) as db:
        assert db.scalar(select(User.email)) == "admin@example.com"
        assert db.scalar(select(User.display_name)) == "Admin"
        assert db.scalar(select(Role.code).join(Membership)) == "platform_admin"
        assert db.scalar(select(func.count()).select_from(SecurityAuditEvent)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    with pytest.raises(ValueError, match="empty identity"):
        module.bootstrap("another@example.com", "Another", "Another Org")
    with Session(admin_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_bootstrap_concurrent_has_one_winner(hidden_password, admin_engine):
    barrier = Barrier(2)

    def attempt(index):
        barrier.wait(timeout=15)
        try:
            module.bootstrap(f"admin{index}@example.com", "Admin", f"Concurrent Org {index}")
            return "created"
        except ValueError as error:
            assert "empty identity" in str(error)
            return "refused"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, (1, 2))) == ["created", "refused"]
    with Session(admin_engine) as db:
        for model in (User, Organization, Membership, SecurityAuditEvent, OutboxEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 1


@pytest.mark.parametrize("name", ["***", "A" * 81])
def test_slug_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="valid slug"):
        module.slugify(name)


@pytest.mark.parametrize("display,organization", [(" ", "Org"), ("Admin", " ")])
def test_bootstrap_requires_names(hidden_password, display, organization):
    with pytest.raises(ValueError, match="required"):
        module.bootstrap("admin@example.com", display, organization)


def test_bootstrap_password_confirmation(monkeypatch):
    answers = iter([PASSWORD, "mismatch"])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(answers))
    with pytest.raises(ValueError, match="confirmation"):
        module.bootstrap("admin@example.com", "Admin", "Org")


def test_bootstrap_missing_system_role_rolls_back(hidden_password, admin_engine):
    with admin_engine.begin() as db:
        db.execute(Role.__table__.delete().where(Role.code == "platform_admin"))
    with pytest.raises(RuntimeError, match="migrations"):
        module.bootstrap("admin@example.com", "Admin", "Org")
    with Session(admin_engine) as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_bootstrap_cli_uses_hidden_input_and_prints_no_credentials(
    monkeypatch, hidden_password, capsys
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap",
            "--email",
            "admin@example.com",
            "--display-name",
            "Admin",
            "--organization-name",
            "CLI Organization",
        ],
    )
    runpy.run_module("app.platform.bootstrap", run_name="__main__")
    output = capsys.readouterr().out
    assert "Bootstrap completed" in output
    assert PASSWORD not in output
    assert "admin@example.com" not in output
