"""Audit integrity and transactional outbox against the ephemeral PostgreSQL database."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import ApplicationError
from app.platform.database.models import Organization, OutboxEvent, SecurityAuditEvent
from app.platform.database.session import set_organization_context
from app.platform.outbox import emit_event
from app.security_audit import service
from app.security_audit.service import _material, record_event, verify_chain


def append(session, organization_id, **changes):
    values = dict(
        organization_id=organization_id,
        request_id="audit-test",
        action="test.changed",
        resource_type="organization",
        resource_id=organization_id,
        outcome="success",
        actor_type="system",
        details={"version": 1},
    )
    values.update(changes)
    return record_event(session, **values)


def emit(session, organization_id, **changes):
    values = dict(
        organization_id=organization_id,
        aggregate_type="organization",
        aggregate_id=organization_id,
        event_type="organization.updated",
        idempotency_key=f"organization.updated:{organization_id}:2",
        payload={"version": 2},
    )
    values.update(changes)
    return emit_event(session, **values)


def test_audit_monotonic_sequence_with_identical_timestamps(db, seeded, monkeypatch):
    fixed_time = datetime(2026, 9, 20, tzinfo=UTC)

    class FrozenClock:
        @staticmethod
        def now(_timezone):
            return fixed_time

    monkeypatch.setattr(service, "datetime", FrozenClock)
    set_organization_context(db, seeded["org_a"])
    assert verify_chain(db, seeded["org_a"]) == (True, None)
    events = [append(db, seeded["org_a"]) for _ in range(3)]
    db.commit()
    set_organization_context(db, seeded["org_a"])
    assert [event.sequence for event in events] == [1, 2, 3]
    assert {event.occurred_at for event in events} == {fixed_time}
    assert verify_chain(db, seeded["org_a"]) == (True, None)


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "organization_id",
        "sequence",
        "hash_version",
        "occurred_at",
        "actor_type",
        "actor_user_id",
        "action",
        "resource_type",
        "resource_id",
        "outcome",
        "request_id",
        "details",
        "prev_hash",
        "event_hash",
    ],
)
def test_audit_detects_each_protected_field(db, admin_engine, seeded, field):
    org = seeded["org_a"]
    set_organization_context(db, org)
    event = append(db, org)
    db.commit()
    changed = {
        "id": uuid4(),
        "organization_id": seeded["org_b"],
        "sequence": 2,
        "hash_version": 2,
        "occurred_at": event.occurred_at + timedelta(seconds=1),
        "actor_type": "user",
        "actor_user_id": seeded["users"]["viewer"],
        "action": "tampered",
        "resource_type": "user",
        "resource_id": uuid4(),
        "outcome": "denied",
        "request_id": "changed-request",
        "details": {"version": 99},
        "prev_hash": "f" * 64,
        "event_hash": "f" * 64,
    }
    with admin_engine.begin() as connection:
        connection.execute(
            update(SecurityAuditEvent)
            .where(SecurityAuditEvent.id == event.id)
            .values(**{field: changed[field]})
        )
    db.expire_all()
    target_org = seeded["org_b"] if field == "organization_id" else org
    set_organization_context(db, target_org)
    valid, invalid_id = verify_chain(db, target_org)
    assert valid is False
    assert invalid_id == str(changed["id"] if field == "id" else event.id)


def test_audit_legacy_hash_contracts_are_explicit():
    event = SecurityAuditEvent(
        id=uuid4(),
        organization_id=uuid4(),
        sequence=1,
        hash_version=1,
        occurred_at=datetime(2026, 9, 20, tzinfo=UTC),
        actor_type="system",
        action="created",
        outcome="success",
        request_id="legacy",
        resource_type="user",
        details={},
    )
    assert _material(event) == (
        b'{"action":"created","outcome":"success","previous":null,"request_id":"legacy"}'
    )
    event.hash_version = 2
    legacy = hashlib.sha256(_material(event)).hexdigest()
    event.hash_version = 3
    assert hashlib.sha256(_material(event)).hexdigest() != legacy
    event.hash_version = 99
    with pytest.raises(ValueError, match="Unsupported"):
        _material(event)


def test_audit_concurrent_writes_serialize_without_forks(runtime_engine, db, seeded):
    barrier = Barrier(4)
    org = seeded["org_a"]

    def write(index):
        with Session(runtime_engine, autoflush=False) as session:
            set_organization_context(session, org)
            barrier.wait(timeout=20)
            event = append(session, org, request_id=f"concurrent-{index}")
            sequence = event.sequence
            session.commit()
            return sequence

    with ThreadPoolExecutor(max_workers=4) as executor:
        sequences = list(executor.map(write, range(4)))
    assert sorted(sequences) == [1, 2, 3, 4]
    set_organization_context(db, org)
    assert verify_chain(db, org) == (True, None)


def test_audit_organization_isolation_and_runtime_append_only(db, seeded):
    for org in (seeded["org_a"], seeded["org_b"]):
        set_organization_context(db, org)
        event = append(db, org)
        assert event.sequence == 1
        db.commit()
    set_organization_context(db, seeded["org_a"])
    assert db.scalar(select(func.count()).select_from(SecurityAuditEvent)) == 1
    assert (
        db.scalar(
            select(SecurityAuditEvent).where(SecurityAuditEvent.organization_id == seeded["org_b"])
        )
        is None
    )
    for statement in (
        "UPDATE security_audit_events SET outcome = 'denied'",
        "DELETE FROM security_audit_events",
    ):
        with pytest.raises(DBAPIError):
            db.execute(text(statement))
        db.rollback()
        set_organization_context(db, seeded["org_a"])


def test_audit_verification_refuses_hidden_history_without_matching_context(db, seeded):
    with pytest.raises(ValueError, match="matching organization context"):
        verify_chain(db, seeded["org_a"])
    set_organization_context(db, seeded["org_b"])
    with pytest.raises(ValueError, match="matching organization context"):
        verify_chain(db, seeded["org_a"])


def test_audit_contract_includes_actor_and_sequence(login):
    auth = login()
    unfiltered = auth["client"].get("/api/v1/security-audit-events")
    assert unfiltered.status_code == 200 and unfiltered.json()["items"]
    response = auth["client"].get(
        "/api/v1/security-audit-events", params={"action": "session.login"}
    )
    assert response.status_code == 200
    event = response.json()["items"][0]
    assert event["actor_type"] == "user"
    assert event["sequence"] == 1
    assert event["hash_version"] == 3


def test_mutation_audit_outbox_rollback_is_atomic(db, seeded):
    org = seeded["org_a"]
    set_organization_context(db, org)
    original_name = db.get(Organization, org).name
    db.get(Organization, org).name = "Should roll back"
    append(db, org)
    emit(db, org)
    db.rollback()
    set_organization_context(db, org)
    assert db.get(Organization, org).name == original_name
    assert db.scalar(select(func.count()).select_from(SecurityAuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    db.get(Organization, org).name = "Committed together"
    append(db, org)
    emit(db, org)
    db.commit()
    set_organization_context(db, org)
    assert db.get(Organization, org).name == "Committed together"
    assert db.scalar(select(func.count()).select_from(SecurityAuditEvent)) == 1
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1


def test_outbox_deduplicates_and_scopes_keys_by_organization(db, seeded):
    key = "same-internal-mutation"
    ids = []
    for org in (seeded["org_a"], seeded["org_b"]):
        set_organization_context(db, org)
        first = emit(db, org, idempotency_key=key)
        second = emit(db, org, idempotency_key=key)
        assert first.id == second.id
        ids.append(first.id)
        db.commit()
    assert len(set(ids)) == 2


@pytest.mark.parametrize(
    "changed",
    [
        {"aggregate_type": "user"},
        {"aggregate_id": uuid4()},
        {"event_type": "organization.deleted"},
        {"payload": {"version": 3}},
    ],
)
def test_outbox_rejects_key_reuse_with_different_content(db, seeded, changed):
    org = seeded["org_a"]
    set_organization_context(db, org)
    emit(db, org)
    with pytest.raises(ApplicationError) as caught:
        emit(db, org, **changed)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("key", ["", "x" * 129, "with space", "contains/secret", "é"])
def test_outbox_rejects_invalid_internal_keys(db, seeded, key):
    with pytest.raises(ValueError, match="safe ASCII"):
        emit(db, seeded["org_a"], idempotency_key=key)


def test_concurrent_outbox_retries_do_not_duplicate(runtime_engine, db, seeded):
    org = seeded["org_a"]
    barrier = Barrier(2)

    def write(_index):
        with Session(runtime_engine, autoflush=False) as session:
            set_organization_context(session, org)
            barrier.wait(timeout=20)
            event_id = emit(session, org).id
            session.commit()
            return event_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(write, range(2)))
    assert ids[0] == ids[1]
    set_organization_context(db, org)
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
