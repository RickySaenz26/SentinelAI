"""SQL invariants use the actual runtime LOGIN, specific SQLSTATE and constraint."""

import json
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.control_reviews.service import ControlReviews
from app.evidence.access import EvidenceAccess
from app.evidence.service import SessionCredentials
from tests import test_control_reviews as reviews

maintenance_engine = reviews.maintenance_engine
setup = reviews.setup
api = reviews.api
flow = reviews.flow


@contextmanager
def actor_db(flow, runtime_engine, who="reviewer", action="decide"):
    client = flow[who]
    headers = flow["reviewer_headers" if who == "reviewer" else "headers"]
    credentials = SessionCredentials(
        client.cookies.get("__Host-sentinel_session"), headers["X-Closure-CSRF"]
    )
    service = ControlReviews(
        EvidenceAccess(sessionmaker(runtime_engine), credentials, "sql-review-test", None)
    )
    with service.transaction() as db:
        actor = service.authorize(db, action)
        yield db, actor


def insert_event(db, org, review_id, **changes):
    values = {
        "org": org,
        "id": uuid4(),
        "review": UUID(review_id),
        "kind": "approved",
        "version": 1,
        "reason": "control_confirmed",
        "cv": 1,
        "ck": {
            key: "confirmed"
            for key in ("console_identity", "inventory_match", "administrative_control")
        },
    }
    values.update(changes)
    values["ck"] = json.dumps(values["ck"]) if values["ck"] is not None else None
    db.execute(
        text(
            "INSERT INTO control_review_events(organization_id,id,review_id,kind,"
            "expected_version,reason_code,checklist_version,checklist) "
            "VALUES(:org,:id,:review,:kind,:version,:reason,:cv,CAST(:ck AS jsonb))"
        ),
        values,
    )


@pytest.mark.parametrize(
    "case,constraint",
    [
        ("version", "control_review_version"),
        ("self", "control_separation"),
        ("reading", "control_reading"),
        ("checklist_version", "control_checklist"),
        ("missing_checklist", "control_checklist"),
        ("extra_checklist", "control_checklist"),
        ("null_checklist", "control_checklist"),
        ("bad_result", "control_checklist"),
        ("not_confirmed", "control_judgement"),
        ("reason", "control_judgement"),
        ("withdraw_other", "control_withdrawal"),
        ("invalid_reference", "control_reference"),
        ("revocation_pending", "control_revocation"),
        ("withdraw_checklist", "control_checklist"),
    ],
)
def test_direct_sql_rejects_specific_violation(
    flow, runtime_engine, admin_engine, case, constraint
):
    request = reviews.created(flow)
    if case != "reading":
        assert flow["reviewer"].get(flow["content"]).status_code == 200
    who = "client" if case in {"self", "withdraw_checklist"} else "reviewer"
    changes = {}
    if case == "version":
        changes["version"] = 99
    elif case == "checklist_version":
        changes["cv"] = 2
    elif case == "missing_checklist":
        changes["ck"] = None
    elif case in {"extra_checklist", "null_checklist", "bad_result", "not_confirmed"}:
        changes["ck"] = {
            key: "confirmed"
            for key in ("console_identity", "inventory_match", "administrative_control")
        }
        key = "extra" if case == "extra_checklist" else "inventory_match"
        changes["ck"][key] = {
            "extra_checklist": "confirmed",
            "null_checklist": None,
            "bad_result": "unknown",
            "not_confirmed": "not_confirmed",
        }[case]
    elif case == "reason":
        changes["reason"] = "incomplete"
    elif case in {"withdraw_other", "withdraw_checklist"}:
        changes.update(kind="withdrawn", reason="presenter_withdrawal")
        if case == "withdraw_other":
            changes.update(cv=None, ck=None)
    elif case == "invalid_reference":
        changes["review"] = uuid4()
    elif case == "revocation_pending":
        changes.update(kind="revoked", reason="error_found", cv=None, ck=None)
    with (
        pytest.raises(DBAPIError) as caught,
        actor_db(flow, runtime_engine, who=who) as (db, actor),
    ):
        insert_event(db, actor.organization_id, request["id"], **changes)
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == constraint
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM control_review_events")) == 1
        assert db.scalar(text("SELECT state FROM control_review_projections")) == "pending"


def test_direct_sql_positive_receipt_atomic_chain_and_no_duplicate(
    flow, runtime_engine, admin_engine, seeded
):
    request = reviews.created(flow)
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    with actor_db(flow, runtime_engine) as (db, actor):
        insert_event(db, actor.organization_id, request["id"])
    with pytest.raises(DBAPIError) as caught, actor_db(flow, runtime_engine) as (db, actor):
        insert_event(db, actor.organization_id, request["id"], version=2)
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "control_pending_current"
    with reviews.Session(admin_engine) as db:
        reviews.set_organization_context(db, seeded["org_a"])
        assert reviews.verify_chain(db, seeded["org_a"]) == (True, None)
        assert (
            db.scalar(
                text("SELECT count(*) FROM control_review_events WHERE read_audit_id IS NOT NULL")
            )
            == 1
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE control_review_requests SET author_id=gen_random_uuid()",
        "DELETE FROM control_review_requests",
        "UPDATE control_review_events SET reason_code='mismatch'",
        "DELETE FROM control_review_events",
        "UPDATE control_review_projections SET state='approved'",
        "INSERT INTO control_review_events(organization_id,id,review_id,kind,actor_id,reason_code) "
        "VALUES(:org,gen_random_uuid(),:review,'approved',gen_random_uuid(),'control_confirmed')",
        "INSERT INTO control_review_requests(organization_id,id,asset_id,presenter_id) "
        "VALUES(:org,gen_random_uuid(),:asset,gen_random_uuid())",
        "SELECT control_log(NULL::control_review_events)",
    ],
)
def test_runtime_cannot_write_reserved_state(flow, runtime_engine, mutation):
    request = reviews.created(flow)
    with pytest.raises(DBAPIError) as caught, actor_db(flow, runtime_engine) as (db, actor):
        db.execute(
            text(mutation),
            {
                "org": actor.organization_id,
                "review": UUID(request["id"]),
                "asset": UUID(flow["asset_id"]),
            },
        )
    assert caught.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("kind", ["submitted", "superseded", "invalidated"])
def test_runtime_cannot_insert_internal_event(flow, runtime_engine, kind):
    request = reviews.created(flow)
    with pytest.raises(DBAPIError) as caught, actor_db(flow, runtime_engine) as (db, actor):
        insert_event(db, actor.organization_id, request["id"], kind=kind)
    assert caught.value.orig.sqlstate == "42501"
    assert caught.value.orig.diag.message_primary == "CONTROL_INTERNAL_EVENT"


def test_rls_and_publisher_cannot_decide(
    flow, runtime_engine, publisher_engine, seeded, admin_engine
):
    reviews.created(flow)
    with actor_db(flow, runtime_engine) as (db, _actor):
        assert db.scalar(text("SELECT count(*) FROM control_review_requests")) == 1
        db.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(seeded["org_b"])},
        )
        for table in (
            "control_review_requests",
            "control_review_events",
            "control_review_projections",
        ):
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    with pytest.raises(DBAPIError) as caught, publisher_engine.begin() as db:
        db.execute(text("SELECT * FROM control_review_requests"))
    assert caught.value.orig.sqlstate == "42501"
    with admin_engine.connect() as db:
        for table in (
            "control_review_requests",
            "control_review_events",
            "control_review_projections",
        ):
            assert db.execute(
                text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:name"),
                {"name": table},
            ).one() == (True, True)
            assert not db.scalar(
                text(
                    "SELECT has_any_column_privilege('sentinelai_policy_publisher',:table,'INSERT')"
                ),
                {"table": table},
            )


@pytest.mark.parametrize("case", ["own", "foreign_author", "wrong_version", "other_tenant"])
def test_request_sql_derived_authority(flow, runtime_engine, seeded, case):
    who = "reviewer" if case == "foreign_author" else "client"

    def execute():
        with actor_db(flow, runtime_engine, who=who, action="submit") as (db, actor):
            db.execute(
                text(
                    "INSERT INTO control_review_requests(organization_id,id,asset_id,"
                    "evidence_id,evidence_version,asset_version) "
                    "VALUES(:org,:id,:asset,:evidence,:version,1)"
                ),
                {
                    "org": seeded["org_b"] if case == "other_tenant" else actor.organization_id,
                    "id": uuid4(),
                    "asset": UUID(flow["asset_id"]),
                    "evidence": UUID(flow["body"]["evidence_id"]),
                    "version": 2 if case == "wrong_version" else 1,
                },
            )

    if case == "own":
        execute()
        with actor_db(flow, runtime_engine, who="client", action="read") as (db, actor):
            assert db.execute(
                text("SELECT presenter_id,author_id FROM control_review_requests")
            ).one() == (actor.user_id, actor.user_id)
    else:
        with pytest.raises(DBAPIError) as caught:
            execute()
        assert caught.value.orig.sqlstate == ("42501" if case == "other_tenant" else "23514")
        if case != "other_tenant":
            assert caught.value.orig.diag.constraint_name == "control_evidence_owner"


def test_sql_rechecks_evidence_read_permission(flow, runtime_engine, admin_engine, seeded):
    request = reviews.created(flow)
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    with pytest.raises(DBAPIError) as caught, actor_db(flow, runtime_engine) as (db, actor):
        with admin_engine.begin() as admin:
            admin.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id=:role AND permission_id="
                    "(SELECT id FROM permissions WHERE code='evidence:read')"
                ),
                {"role": seeded["roles"]["security_manager"]},
            )
        insert_event(db, actor.organization_id, request["id"])
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "control_separation"
