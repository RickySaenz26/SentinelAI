"""4B integration: real PostgreSQL, private tmpfs evidence, no target traffic."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.control_reviews.service import ControlReviews
from app.main import app
from app.platform.database.session import set_organization_context
from app.security_audit.service import verify_chain
from tests import test_evidence_api as evidence_tests
from tests.test_asset_migration import revision
from tests.test_assets import POLICY

maintenance_engine = evidence_tests.maintenance_engine
setup = evidence_tests.setup
api = evidence_tests.api


@pytest.fixture
def flow(api, client, login):
    evidence, content_path = evidence_tests.stored(client, api)
    with TestClient(app, base_url="https://testserver") as reviewer:
        auth = login("security_manager", client=reviewer)
        yield {
            "client": client,
            "reviewer": reviewer,
            "headers": api["headers"],
            "reviewer_headers": auth["headers"],
            "asset_id": str(api["asset_id"]),
            "content": content_path + "/content",
            "path": f"/api/v1/assets/{api['asset_id']}/control-reviews",
            "body": {"evidence_id": evidence["id"], "evidence_version": evidence["version"]},
            "api": api,
        }


def submit(flow, **changes):
    return flow["client"].post(
        flow["path"],
        json={**flow["body"], **changes},
        headers={**flow["headers"], "Idempotency-Key": uuid4().hex},
    )


def decide(flow, review, *, who="reviewer", kind="approved", version=1, **changes):
    body = {
        "decision": kind,
        "checklist_version": 1,
        "checklist": {
            key: "confirmed"
            for key in ("administrative_control", "console_identity", "inventory_match")
        },
        "reason_code": "control_confirmed" if kind == "approved" else "mismatch",
    }
    return flow[who].post(
        f"/api/v1/control-reviews/{review}/decisions",
        json={**body, **changes},
        headers={
            **flow["reviewer_headers" if who == "reviewer" else "headers"],
            "If-Match": str(version),
            "Idempotency-Key": uuid4().hex,
        },
    )


def created(flow):
    response = submit(flow)
    assert response.status_code == 201, response.text
    return response.json()


def test_complete_history_and_current_status(flow, admin_engine, seeded):
    request = created(flow)
    assert request["state"] == "pending" and request["version"] == 1
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    response = decide(flow, request["id"])
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "approved"
    approved = response.json()
    assert datetime.fromisoformat(approved["valid_until"]) - datetime.fromisoformat(
        approved["decided_at"]
    ) == timedelta(days=30)
    status = flow["client"].get(f"/api/v1/assets/{flow['asset_id']}/control-status")
    assert status.status_code == 200, status.text
    assert status.json()["valid"] is True and status.json()["reasons"] == []
    assert status.headers["Cache-Control"] == "no-store"
    page = flow["client"].get(flow["path"])
    assert page.status_code == 200, page.text
    assert len(page.json()["items"]) == 1
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT ownership_status FROM assets")) == "unverified"
        assert db.scalar(text("SELECT count(*) FROM control_review_events")) == 2
        assert (
            db.scalar(text("SELECT count(*) FROM outbox_events WHERE event_type LIKE 'control.%'"))
            == 2
        )
    with Session(admin_engine) as db:
        set_organization_context(db, seeded["org_a"])
        assert verify_chain(db, seeded["org_a"]) == (True, None)
        assert (
            db.scalar(
                text("SELECT count(*) FROM security_audit_events WHERE action LIKE 'control.%'")
            )
            == 2
        )


def mutate(flow, review, suffix, reason, *, who="client", version=1, key=None):
    return flow[who].post(
        f"/api/v1/control-reviews/{review}/{suffix}",
        json={"reason_code": reason},
        headers={
            **flow["headers" if who == "client" else "reviewer_headers"],
            "If-Match": str(version),
            "Idempotency-Key": key or uuid4().hex,
        },
    )


def state(flow):
    response = flow["client"].get(f"/api/v1/assets/{flow['asset_id']}/control-status")
    assert response.status_code == 200, response.text
    return response.json()


def approve(flow):
    request = created(flow)
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    result = decide(flow, request["id"])
    assert result.status_code == 201, result.text
    return result.json()


def test_reading_is_after_request_and_not_automatic(flow):
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    request = created(flow)
    response = decide(flow, request["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONTROL_READING_REQUIRED"
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    assert decide(flow, request["id"]).status_code == 201


@pytest.mark.parametrize("different_session", [False, True])
def test_self_approval_even_another_session_or_role(
    flow, login, admin_engine, seeded, different_session
):
    request = created(flow)
    if different_session:
        flow["headers"].update(login()["headers"])
        with admin_engine.begin() as db:
            db.execute(
                text("UPDATE memberships SET role_id=:role WHERE id=:id"),
                {
                    "role": seeded["roles"]["security_manager"],
                    "id": seeded["memberships"]["org_owner"],
                },
            )
    assert flow["client"].get(flow["content"]).status_code == 200
    response = decide(flow, request["id"], who="client")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONTROL_SEPARATION_REQUIRED"


@pytest.mark.parametrize(
    "role", ["org_owner", "security_manager", "analyst", "auditor", "viewer", "platform_admin"]
)
def test_roles_own_evidence_only_and_minimal_summary(flow, login, role, admin_engine):
    request = created(flow)
    flow["headers"].update(login(role)["headers"])
    full = flow["client"].get(f"/api/v1/control-reviews/{request['id']}")
    expected = (
        200
        if role in {"org_owner", "security_manager", "auditor"}
        else (404 if role == "analyst" else 403)
    )
    assert full.status_code == expected
    result = submit(flow)
    assert result.status_code == (
        409 if role == "org_owner" else (404 if role in {"security_manager", "analyst"} else 403)
    ), result.text
    summary = flow["client"].get(f"/api/v1/assets/{flow['asset_id']}/control-summary")
    assert summary.status_code == 200, summary.text
    assert summary.json() == {
        "asset_id": flow["asset_id"],
        "review_count": 1,
        "ownership_status": "unverified",
    }
    if role in {"analyst", "auditor", "viewer", "platform_admin"}:
        assert decide(flow, request["id"], who="client").status_code == 403
        assert mutate(flow, request["id"], "revocations", "error_found").status_code == 403


def test_analyst_can_submit_read_and_withdraw_only_own(flow, login):
    flow["headers"].update(login("analyst")["headers"])
    response = evidence_tests.present(
        flow["client"], flow["api"], **{"Idempotency-Key": "analyst-evidence"}
    )
    assert response.status_code == 201, response.text
    flow["body"] = {
        "evidence_id": response.json()["id"],
        "evidence_version": response.json()["version"],
    }
    request = created(flow)
    assert flow["client"].get(f"/api/v1/control-reviews/{request['id']}").status_code == 200
    assert (
        mutate(
            flow, request["id"], "withdrawals", "presenter_withdrawal", who="reviewer"
        ).status_code
        == 409
    )
    assert mutate(flow, request["id"], "withdrawals", "presenter_withdrawal").status_code == 201
    assert state(flow)["reasons"] == ["no_approval"]


def test_renewal_rejection_withdrawal_supersession_and_revocation(flow):
    old = approve(flow)
    for terminal in ("rejected", "withdrawn"):
        request = created({**flow, "body": {**flow["body"], "renews_review_id": old["id"]}})
        if terminal == "rejected":
            assert flow["reviewer"].get(flow["content"]).status_code == 200
            response = decide(flow, request["id"], kind="rejected")
        else:
            response = mutate(flow, request["id"], "withdrawals", "presenter_withdrawal")
        assert response.status_code == 201, response.text
        assert state(flow)["valid"] is True
        historical = flow["client"].get(f"/api/v1/control-reviews/{old['id']}").json()
        assert historical["valid_until"] == old["valid_until"]
    replacement = approve({**flow, "body": {**flow["body"], "renews_review_id": old["id"]}})
    previous = flow["client"].get(f"/api/v1/control-reviews/{old['id']}").json()
    assert previous["superseded_by"] == replacement["id"]
    revoked = mutate(flow, replacement["id"], "revocations", "confidence_withdrawn", version=2)
    assert revoked.status_code == 201, revoked.text
    assert state(flow)["reasons"] == ["revoked"]  # never restore the previous approval
    assert state(flow)["review_id"] == replacement["id"]
    again = mutate(flow, replacement["id"], "revocations", "error_found", version=3)
    assert again.status_code == 409


@pytest.mark.parametrize("when", ["pending", "approved"])
def test_metadata_compatibility_archive_and_policy_generation(flow, publish_policy, when):
    request = approve(flow) if when == "approved" else created(flow)
    response = flow["client"].patch(
        f"/api/v1/assets/{flow['asset_id']}",
        json={"display_name": "Updated lab label", "criticality": "high"},
        headers={**flow["headers"], "Idempotency-Key": "metadata"},
    )
    assert response.status_code == 200, response.text
    historical = flow["client"].get(f"/api/v1/control-reviews/{request['id']}").json()
    assert historical["state"] == when and historical["asset_version"] == 1
    if when == "approved":
        assert state(flow)["valid"]
    publish_policy({**POLICY, "allowed_targets": []})
    publish_policy(POLICY)  # identical hash, different generation: cannot resurrect
    historical = flow["client"].get(f"/api/v1/control-reviews/{request['id']}").json()
    assert historical["invalidated_at"] is not None
    assert historical["state"] == ("invalidated" if when == "pending" else "approved")
    assert not state(flow)["valid"]
    flow["headers"]["If-Match"] = "2"
    if when == "approved":
        flow["body"]["renews_review_id"] = request["id"]
    renewed = created(flow)  # reuses retained evidence but new request/current generation
    assert renewed["generation"] != request["generation"]
    response = flow["client"].request(
        "DELETE",
        f"/api/v1/assets/{flow['asset_id']}",
        json={"reason": "lab retired"},
        headers={**flow["headers"], "Idempotency-Key": "archive"},
    )
    assert response.status_code == 204, response.text
    historical = flow["client"].get(f"/api/v1/control-reviews/{renewed['id']}").json()
    assert historical["state"] == "invalidated"


@pytest.mark.parametrize("failure", ["key", "corrupt", "absent"])
def test_current_storage_is_checked_and_never_cached(flow, failure):
    approve(flow)
    assert state(flow)["valid"]
    root = flow["api"]["root"]
    if failure == "key":
        (root / "keys" / "lab-1.kek").unlink()
    else:
        path = next((root / "storage").iterdir())
        if failure == "corrupt":
            path.write_bytes(b"invalid")
        else:
            path.unlink()
    assert state(flow)["reasons"] == ["evidence_unavailable"]


@pytest.mark.parametrize(
    "case,status",
    [
        ("csrf", 403),
        ("origin", 403),
        ("cookie", 401),
        ("version", 409),
        ("missing_version", 422),
        ("missing_key", 422),
        ("extra", 422),
        ("duplicate", 422),
        ("invalid", 422),
        ("large", 413),
        ("stream", 413),
        ("media", 415),
        ("wrong_evidence_version", 404),
        ("unknown", 404),
        ("bool", 422),
    ],
)
def test_strict_input(flow, case, status):
    headers = {**flow["headers"], "Idempotency-Key": "strict-control"}
    body = dict(flow["body"])
    if case == "csrf":
        headers.pop("X-Closure-CSRF")
    elif case == "origin":
        headers["Origin"] = "https://untrusted.example"
    elif case == "cookie":
        flow["client"].cookies.clear()
    elif case == "version":
        headers["If-Match"] = "2"
    elif case == "missing_version":
        headers.pop("If-Match")
    elif case == "missing_key":
        headers.pop("Idempotency-Key")
    elif case == "extra":
        body["tenant_id"] = str(uuid4())
    elif case == "media":
        headers["Content-Type"] = "text/plain"
    elif case == "wrong_evidence_version":
        body["evidence_version"] = 2
    elif case == "unknown":
        body["evidence_id"] = str(uuid4())
    elif case == "bool":
        body["evidence_version"] = True
    raw = json.dumps(body)
    if case == "duplicate":
        raw = raw[:-1] + ',"evidence_version":1}'
    elif case == "invalid":
        raw = "{"
    elif case in {"large", "stream"}:
        raw = "x" * 16385
    if case == "stream":
        raw = iter([raw[:8000].encode(), raw[8000:].encode()])
    result = flow["client"].post(flow["path"], content=raw, headers=headers)
    assert result.status_code == status, result.text
    assert result.headers["Cache-Control"] == "no-store"


def test_replay_reauthorizes_and_is_historical(flow, admin_engine):
    headers = {**flow["headers"], "Idempotency-Key": "same"}
    first = flow["client"].post(flow["path"], json=flow["body"], headers=headers)
    assert first.status_code == 201, first.text
    request = first.json()
    assert mutate(flow, request["id"], "withdrawals", "presenter_withdrawal").status_code == 201
    replayed = flow["client"].post(flow["path"], json=flow["body"], headers=headers)
    assert replayed.status_code == 201 and replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json()["id"] == request["id"] and replayed.json()["state"] == "withdrawn"
    changed = flow["client"].post(
        flow["path"], json=flow["body"], headers={**headers, "If-Match": "2"}
    )
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with admin_engine.begin() as db:
        rows = (
            db.execute(
                text(
                    "SELECT response_body FROM http_idempotency_records "
                    "WHERE route LIKE '%control-reviews%'"
                )
            )
            .scalars()
            .all()
        )
        assert all(set(row) == {"review_id", "event_id"} for row in rows)
        db.execute(
            text(
                "UPDATE http_idempotency_records SET "
                "created_at=clock_timestamp()-interval '2 days', "
                "expires_at=clock_timestamp()"
                "-interval '1 second' WHERE route LIKE '%control-reviews%'"
            )
        )
    expired = flow["client"].post(flow["path"], json=flow["body"], headers=headers)
    assert expired.status_code == 201 and expired.json()["id"] != request["id"]
    flow["client"].cookies.clear()
    assert flow["client"].post(flow["path"], json=flow["body"], headers=headers).status_code == 401


@pytest.mark.parametrize("committed", [False, True])
def test_rollback_and_uncertain_commit_recovery(flow, monkeypatch, admin_engine, committed):
    original = ControlReviews.commit
    injected = False

    def failure(db):
        nonlocal injected
        pending = db.scalar(text("SELECT count(*) FROM control_review_requests"))
        if pending and not injected:
            injected = True
            if committed:
                original(db)
            raise OSError("Injected transport loss; not an approval result")
        original(db)

    headers = {**flow["headers"], "Idempotency-Key": "uncertain"}
    monkeypatch.setattr(ControlReviews, "commit", staticmethod(failure))
    result = flow["client"].post(flow["path"], json=flow["body"], headers=headers)
    assert result.status_code == 503 and result.json()["error"]["code"] == "CONTROL_UNAVAILABLE"
    with admin_engine.connect() as db:
        for table in ("control_review_requests", "control_review_events"):
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == int(committed)
        assert db.scalar(
            text("SELECT count(*) FROM outbox_events WHERE event_type='control.submitted'")
        ) == int(committed)
    monkeypatch.setattr(ControlReviews, "commit", staticmethod(original))
    retry = flow["client"].post(flow["path"], json=flow["body"], headers=headers)
    assert (
        retry.status_code == 201 and retry.headers["Idempotency-Replayed"] == str(committed).lower()
    )
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM control_review_events")) == 1


@pytest.mark.parametrize(
    "change,expected",
    [("session", 401), ("permission", 403), ("archive", 403), ("generation", 409)],
)
def test_changes_during_storage_io_revalidated(
    flow, monkeypatch, admin_engine, seeded, publish_policy, change, expected
):
    original = ControlReviews.read_storage

    def racing(self, row):
        original(self, row)
        # A separate connection must acquire the org lock now: no DB lock spans I/O.
        with admin_engine.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='2s'"))
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:org,1))"),
                {"org": str(seeded["org_a"])},
            )
            if change == "session":
                db.execute(
                    text("UPDATE sessions SET revoked_at=clock_timestamp() WHERE user_id=:id"),
                    {"id": seeded["users"]["org_owner"]},
                )
            elif change == "permission":
                db.execute(
                    text(
                        "DELETE FROM role_permissions WHERE role_id=:role AND permission_id="
                        "(SELECT id FROM permissions WHERE code='control:submit')"
                    ),
                    {"role": seeded["roles"]["org_owner"]},
                )
            elif change == "archive":
                db.execute(
                    text(
                        "UPDATE assets SET deleted_at=clock_timestamp(),archive_reason='retired',"
                        "version=version+1 WHERE id=:id"
                    ),
                    {"id": UUID(flow["asset_id"])},
                )
        if change == "generation":
            publish_policy({**POLICY, "allowed_targets": []})
            publish_policy(POLICY)

    monkeypatch.setattr(ControlReviews, "read_storage", racing)
    result = submit(flow)
    assert result.status_code == expected, result.text
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM control_review_requests")) == 0


def test_two_reviewers_serialize_exactly_one_decision(
    flow, login, create_user, seeded, monkeypatch
):
    request = created(flow)
    user = create_user(role="security_manager", organization_id=seeded["org_a"])
    with TestClient(app, base_url="https://testserver") as other:
        result = other.post(
            "/api/v1/session", json={"email": user["email"], "password": user["password"]}
        )
        assert result.status_code == 200
        alternate = {
            **flow,
            "reviewer": other,
            "reviewer_headers": {"X-Closure-CSRF": result.json()["csrf_token"]},
        }
        assert other.get(flow["content"]).status_code == 200
        assert flow["reviewer"].get(flow["content"]).status_code == 200
        barrier, original = Barrier(2), ControlReviews.read_storage

        def racing(self, row):
            original(self, row)
            barrier.wait(timeout=10)

        monkeypatch.setattr(ControlReviews, "read_storage", racing)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(decide, f, request["id"]) for f in (flow, alternate)]
            results = [future.result(timeout=20) for future in futures]
        assert sorted(r.status_code for r in results) == [201, 409]
        loser = next(r for r in results if r.status_code == 409)
        assert loser.json()["error"]["code"] == "VERSION_CONFLICT"


def test_thirty_days_and_original_retention_limit(flow, admin_engine):
    with admin_engine.begin() as db:
        db.execute(
            text("UPDATE evidence_versions SET created_at=clock_timestamp()-interval '80 days'")
        )
    review = approve(flow)
    until = datetime.fromisoformat(review["valid_until"])
    retained = datetime.fromisoformat(review["retention_until"])
    decided = datetime.fromisoformat(review["decided_at"])
    assert until == retained and timedelta(days=9) < until - decided < timedelta(days=10)
    # Guarded test DB clock simulation. Request history stays immutable in application grants.
    with admin_engine.begin() as db:
        db.execute(
            text(
                "UPDATE control_review_projections SET valid_until=clock_timestamp()"
                "-interval '1 second'"
            )
        )
    assert state(flow)["reasons"] == ["expired"]


def test_expired_evidence_cannot_be_presented(flow, admin_engine):
    with admin_engine.begin() as db:
        db.execute(
            text("UPDATE evidence_versions SET created_at=clock_timestamp()-interval '91 days'")
        )
    result = submit(flow)
    assert result.status_code == 410
    assert result.json()["error"]["code"] == "EVIDENCE_RETENTION_EXPIRED"


def test_downgrade_refuses_any_review_history(flow, admin_engine):
    created(flow)
    result = revision(
        admin_engine.url.render_as_string(hide_password=False), "downgrade", "20261007_08"
    )
    assert result.returncode != 0 and "refuses control-review history" in result.stderr
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261009_09"
        assert db.scalar(text("SELECT count(*) FROM control_review_requests")) == 1


def test_foreign_tenant_http_cannot_discover_or_mutate(flow, create_user, seeded):
    review = created(flow)
    user = create_user(role="org_owner", organization_id=seeded["org_b"])
    with TestClient(app, base_url="https://testserver") as other:
        auth = other.post(
            "/api/v1/session", json={"email": user["email"], "password": user["password"]}
        )
        assert auth.status_code == 200
        for path in (
            flow["path"],
            f"/api/v1/control-reviews/{review['id']}",
            f"/api/v1/assets/{flow['asset_id']}/control-status",
            f"/api/v1/assets/{flow['asset_id']}/control-summary",
        ):
            assert other.get(path).status_code == 404
        alternate = {
            **flow,
            "reviewer": other,
            "reviewer_headers": {"X-Closure-CSRF": auth.json()["csrf_token"]},
        }
        assert decide(alternate, review["id"]).status_code == 404


@pytest.mark.parametrize("phase", ["decision", "status"])
def test_publication_during_read_prevents_positive_response(
    flow, monkeypatch, publish_policy, phase
):
    request = approve(flow) if phase == "status" else created(flow)
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    original = ControlReviews.read_storage

    def racing(self, row):
        original(self, row)
        publish_policy({**POLICY, "allowed_targets": []})
        publish_policy(POLICY)

    monkeypatch.setattr(ControlReviews, "read_storage", racing)
    if phase == "status":
        result = state(flow)
        assert not result["valid"] and "admission_changed" in result["reasons"]
    else:
        result = decide(flow, request["id"])
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "VERSION_CONFLICT"


def test_concurrent_same_key_is_one_request_and_replay(flow, monkeypatch, admin_engine):
    original, barrier = ControlReviews.read_storage, Barrier(2)

    def racing(self, row):
        original(self, row)
        barrier.wait(timeout=10)

    monkeypatch.setattr(ControlReviews, "read_storage", racing)
    headers = {**flow["headers"], "Idempotency-Key": "concurrent-request"}
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(flow["client"].post, flow["path"], json=flow["body"], headers=headers)
            for _ in range(2)
        ]
        results = [future.result(timeout=20) for future in futures]
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()["id"] for r in results}) == 1
    assert sorted(r.headers["Idempotency-Replayed"] for r in results) == ["false", "true"]
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM control_review_events")) == 1


def test_pagination_and_unknown_asset(flow):
    first = created(flow)
    assert mutate(flow, first["id"], "withdrawals", "presenter_withdrawal").status_code == 201
    second = created(flow)
    page = flow["client"].get(flow["path"], params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_id"] is not None
    following = (
        flow["client"].get(flow["path"], params={"limit": 1, "after": page["next_id"]}).json()
    )
    assert {page["items"][0]["id"], following["items"][0]["id"]} == {first["id"], second["id"]}
    assert following["next_id"] is None
    assert flow["client"].get(flow["path"], params={"limit": 101}).status_code == 422
    assert flow["client"].get(f"/api/v1/assets/{uuid4()}/control-summary").status_code == 404


@pytest.mark.parametrize("committed", [False, True])
def test_decision_commit_failure_keeps_atomic_history(flow, monkeypatch, admin_engine, committed):
    request = created(flow)
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    original = ControlReviews.commit
    injected = False

    def failure(db):
        nonlocal injected
        approved = db.scalar(
            text("SELECT count(*) FROM control_review_projections WHERE state='approved'")
        )
        if approved and not injected:
            injected = True
            if committed:
                original(db)
            raise OSError("Test-only uncertain transport")
        original(db)

    headers = {**flow["reviewer_headers"], "If-Match": "1", "Idempotency-Key": "decision-uncertain"}
    body = {
        "decision": "approved",
        "checklist_version": 1,
        "reason_code": "control_confirmed",
        "checklist": {
            k: "confirmed"
            for k in ("console_identity", "inventory_match", "administrative_control")
        },
    }
    path = f"/api/v1/control-reviews/{request['id']}/decisions"
    monkeypatch.setattr(ControlReviews, "commit", staticmethod(failure))
    response = flow["reviewer"].post(path, json=body, headers=headers)
    assert response.status_code == 503 and "Test-only" not in response.text
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT state FROM control_review_projections")) == (
            "approved" if committed else "pending"
        )
        for table, column in (("security_audit_events", "action"), ("outbox_events", "event_type")):
            assert db.scalar(
                text(f"SELECT count(*) FROM {table} WHERE {column}='control.approved'")
            ) == int(committed)
    monkeypatch.setattr(ControlReviews, "commit", staticmethod(original))
    retried = flow["reviewer"].post(path, json=body, headers=headers)
    assert (
        retried.status_code == 201
        and retried.headers["Idempotency-Replayed"] == str(committed).lower()
    )
    with admin_engine.connect() as db:
        assert (
            db.scalar(text("SELECT count(*) FROM control_review_events WHERE kind='approved'")) == 1
        )


@pytest.mark.parametrize(
    "change,status",
    [("session", 401), ("role", 403), ("permission", 403), ("evidence_permission", 403)],
)
def test_reviewer_authority_changes_during_io(
    flow, monkeypatch, admin_engine, seeded, change, status
):
    request = created(flow)
    assert flow["reviewer"].get(flow["content"]).status_code == 200
    original = ControlReviews.read_storage

    def racing(self, row):
        original(self, row)
        with admin_engine.begin() as db:
            if change == "session":
                db.execute(
                    text("UPDATE sessions SET revoked_at=clock_timestamp() WHERE user_id=:id"),
                    {"id": seeded["users"]["security_manager"]},
                )
            elif change == "role":
                db.execute(
                    text("UPDATE memberships SET role_id=:role WHERE id=:id"),
                    {
                        "role": seeded["roles"]["viewer"],
                        "id": seeded["memberships"]["security_manager"],
                    },
                )
            else:
                db.execute(
                    text(
                        "DELETE FROM role_permissions WHERE role_id=:role AND permission_id="
                        "(SELECT id FROM permissions WHERE code=:permission)"
                    ),
                    {
                        "role": seeded["roles"]["security_manager"],
                        "permission": "evidence:read"
                        if change == "evidence_permission"
                        else "control:decide",
                    },
                )

    monkeypatch.setattr(ControlReviews, "read_storage", racing)
    response = decide(flow, request["id"])
    assert response.status_code == status, response.text
    with admin_engine.connect() as db:
        assert db.scalar(text("SELECT state FROM control_review_projections")) == "pending"


def test_renewal_requires_link_and_no_target_connections(flow, monkeypatch):
    import socket

    original = socket.getaddrinfo

    def guard(host, *args, **kwargs):
        assert str(host) not in POLICY["allowed_targets"]
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guard)
    approved = approve(flow)
    assert submit(flow).status_code == 409
    result = submit(flow, renews_review_id=approved["id"])
    assert result.status_code == 201, result.text


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_checklist_version_is_strict_integer_catalog(flow, version):
    request = created(flow)
    result = decide(flow, request["id"], checklist_version=version)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "INVALID_CONTROL_REQUEST"
