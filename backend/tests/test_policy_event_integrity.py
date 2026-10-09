"""Restricted LOGIN attacks reach the intended SQL constraint, not an invalid fixture."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.assets.policy import LabPolicy
from app.platform.database.session import set_organization_context
from app.security_audit.service import record_event, verify_chain
from tests.test_assets import POLICY, create
from tests.test_policy_authority import history


def revision(db, org, *, sequence=1, policy=None):
    policy = LabPolicy.model_validate_json(json.dumps(policy or POLICY))
    key = uuid4()
    db.execute(
        text(
            "INSERT INTO lab_policy_revisions "
            "(organization_id,id,sequence,snapshot,policy_hash,provenance) "
            "VALUES(:org,:id,:sequence,:snapshot,:hash,'declared-fixture')"
        ),
        dict(
            org=org,
            id=key,
            sequence=sequence,
            snapshot=policy.model_dump_json(),
            hash=policy.fingerprint,
        ),
    )
    return key, dict(
        sequence=sequence,
        policy_hash=policy.fingerprint,
        publisher=db.scalar(text("SELECT session_user")),
        provenance="declared-fixture",
    )


def audit(db, org, key, content, **patch):
    arguments = dict(
        request_id=str(key),
        action="lab_policy.published",
        resource_type="lab_policy",
        resource_id=key,
        outcome="success",
        organization_id=org,
        actor_type="system",
        details=content,
    )
    record_event(db, **(arguments | patch))


def outbox(db, org, key, content, *, reserved=None, event_key=None):
    columns = (
        "id,organization_id,aggregate_type,aggregate_id,event_type,"
        "idempotency_key,payload,created_at"
    )
    values = (
        ":id,:org,'lab_policy',:key,'lab_policy.published',:event_key,CAST(:payload AS json),now()"
    )
    if reserved is not None:
        assert reserved in ("published_at", "attempts")
        columns += "," + reserved
        values += ",now()" if reserved == "published_at" else ",1"
    db.execute(
        text(f"INSERT INTO outbox_events ({columns}) VALUES ({values})"),
        dict(
            id=uuid4(),
            org=org,
            key=key,
            event_key=event_key or f"lab_policy:{key}",
            payload=json.dumps(content),
        ),
    )


def assert_error(error, state, constraint=None):
    assert error.value.orig.sqlstate == state
    if constraint:
        assert error.value.orig.diag.constraint_name == constraint


def assert_empty(admin_engine):
    with admin_engine.connect() as db:
        for table in (
            "lab_policy_revisions",
            "lab_policy_current",
            "security_audit_events",
            "outbox_events",
        ):
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_valid_direct_sql_publication(publish_policy, publisher_engine, seeded, admin_engine):
    org = seeded["org_a"]
    with Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org)
        audit(db, org, key, content)
        outbox(db, org, key, content)
        db.commit()
        set_organization_context(db, org)
        assert verify_chain(db, org) == (True, None)
        assert db.execute(text("SELECT published_at,attempts FROM outbox_events")).one() == (
            None,
            0,
        )
        assert db.scalar(text("SELECT publisher=session_user FROM lab_policy_revisions"))
        set_organization_context(db, seeded["org_b"])
        assert db.scalar(text("SELECT count(*) FROM lab_policy_revisions")) == 0
        assert db.scalar(text("SELECT count(*) FROM outbox_events")) == 0


@pytest.mark.parametrize("event", ["audit", "outbox"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_hash", "0" * 64),
        ("sequence", 99),
        ("provenance", "forged"),
        ("publisher", "someone_else"),
        ("unexpected", True),
    ],
)
def test_contradictory_content_rolls_back(
    publish_policy, publisher_engine, seeded, admin_engine, event, field, value
):
    org = seeded["org_a"]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org)
        audit(db, org, key, content | {field: value} if event == "audit" else content)
        outbox(db, org, key, content | {field: value} if event == "outbox" else content)
        db.commit()
    assert_error(error, "23514", "policy_event_content")
    assert_empty(admin_engine)


@pytest.mark.parametrize("reserved", ["published_at", "attempts"])
def test_delivery_columns_denied(publish_policy, publisher_engine, seeded, admin_engine, reserved):
    org = seeded["org_a"]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org)
        audit(db, org, key, content)
        outbox(db, org, key, content, reserved=reserved)
        db.commit()
    assert_error(error, "42501")
    assert "permission denied for table outbox_events" in str(error.value.orig)
    assert_empty(admin_engine)


@pytest.mark.parametrize("missing", ["audit", "outbox", "both"])
def test_missing_event_rolls_back(publish_policy, publisher_engine, seeded, admin_engine, missing):
    org = seeded["org_a"]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org)
        if missing == "outbox":
            audit(db, org, key, content)
        if missing == "audit":
            outbox(db, org, key, content)
        db.commit()
    assert_error(error, "23514", "policy_events_required")
    assert_empty(admin_engine)


@pytest.mark.parametrize("event", ["audit", "outbox"])
def test_duplicate_event_rolls_back(publish_policy, publisher_engine, seeded, admin_engine, event):
    org = seeded["org_a"]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org)
        audit(db, org, key, content)
        outbox(db, org, key, content)
        (audit if event == "audit" else outbox)(db, org, key, content)
        db.commit()
    constraint = (
        "uq_policy_audit_revision" if event == "audit" else "uq_outbox_organization_idempotency_key"
    )
    assert_error(error, "23505", constraint)
    assert_empty(admin_engine)


@pytest.mark.parametrize("event", ["audit", "outbox"])
def test_runtime_cannot_impersonate_publisher(
    publish_policy, publisher_engine, runtime_engine, seeded, event
):
    receipt = publish_policy(POLICY)
    org = seeded["org_a"]
    with Session(publisher_engine) as db:
        set_organization_context(db, org)
        row = db.execute(text("SELECT * FROM lab_policy_revisions")).mappings().one()
        content = {
            field: row[field] for field in ("sequence", "policy_hash", "publisher", "provenance")
        }
    assert str(row["id"]) == receipt["revision_id"]
    with pytest.raises(DBAPIError) as error, Session(runtime_engine) as db:
        set_organization_context(db, org)
        (audit if event == "audit" else outbox)(db, org, row["id"], content)
        db.commit()
    assert_error(error, "23514", "policy_event_authority")


@pytest.mark.parametrize("event", ["audit", "outbox"])
def test_later_transaction_cannot_duplicate_publication(
    publish_policy, publisher_engine, seeded, event
):
    publish_policy(POLICY)
    org = seeded["org_a"]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        row = db.execute(text("SELECT * FROM lab_policy_revisions")).mappings().one()
        content = {
            field: row[field] for field in ("sequence", "policy_hash", "publisher", "provenance")
        }
        (audit if event == "audit" else outbox)(db, org, row["id"], content)
        db.commit()
    assert_error(
        error,
        "23505",
        "uq_policy_audit_revision"
        if event == "audit"
        else "uq_outbox_organization_idempotency_key",
    )


@pytest.mark.parametrize("event", ["audit", "outbox"])
@pytest.mark.parametrize("reference", ["unknown", "other-tenant"])
def test_unlinked_event_rolls_back(
    publish_policy, publisher_engine, seeded, admin_engine, event, reference
):
    org = seeded["org_a"]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org)
        if reference == "other-tenant":
            org = seeded["org_b"]
            set_organization_context(db, org)
        else:
            key = uuid4()
        (audit if event == "audit" else outbox)(db, org, key, content)
        db.commit()
    assert_error(error, "23514", "policy_event_authority")
    assert_empty(admin_engine)


@pytest.mark.parametrize("event", ["audit", "outbox"])
def test_request_identity_bound(publish_policy, publisher_engine, seeded, admin_engine, event):
    org = seeded["org_a"]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org)
        if event == "audit":
            audit(db, org, key, content, request_id=str(uuid4()))
        else:
            outbox(db, org, key, content, event_key="unlinked")
        db.commit()
    assert_error(error, "23514", "policy_event_content")
    assert_empty(admin_engine)


def test_failed_sql_withdrawal_restores_admissions(
    publish_policy, publisher_engine, seeded, client, login, runtime_engine, admin_engine
):
    publish_policy(POLICY)
    assert create(client, login()).status_code == 201
    org = seeded["org_a"]
    with admin_engine.connect() as db:
        before = [
            db.scalar(text(f"SELECT count(*) FROM {t}"))
            for t in ("security_audit_events", "outbox_events", "lab_policy_revisions")
        ]
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, org)
        key, content = revision(db, org, sequence=2, policy=POLICY | {"allowed_targets": []})
        audit(db, org, key, content)
        outbox(db, org, key, content | {"provenance": "forged"})
        db.commit()
    assert_error(error, "23514", "policy_event_content")
    assert history(runtime_engine, org) == [(1, True, "created")]
    with admin_engine.connect() as db:
        assert before == [
            db.scalar(text(f"SELECT count(*) FROM {t}"))
            for t in ("security_audit_events", "outbox_events", "lab_policy_revisions")
        ]
