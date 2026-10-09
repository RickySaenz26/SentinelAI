"""Published synthetic policy, restricted LOGINs and persistent admission fences."""

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.assets import publisher as publication_module
from app.assets import service as asset_service
from app.assets.authority import require_admission
from app.assets.policy import LabPolicy
from app.assets.publisher import PolicyPublisher, main
from app.core.errors import ApplicationError
from app.platform.database.session import set_organization_context
from app.security_audit.service import verify_chain
from tests import test_evidence_service
from tests.test_assets import POLICY, create
from tests.test_postgres_security import denied

maintenance_engine = test_evidence_service.maintenance_engine


def publisher(engine, org):
    return PolicyPublisher(sessionmaker(engine), org)


def history(engine, org):
    with Session(engine) as session:
        set_organization_context(session, org)
        return session.execute(
            text(
                "SELECT generation,admitted,reason FROM asset_admission_history ORDER BY generation"
            )
        ).all()


def test_no_environment_fallback_and_generation_history(
    client, login, seeded, publish_policy, publisher_engine, runtime_engine, monkeypatch
):
    auth = login()
    monkeypatch.setenv("LAB_ASSET_POLICY_JSON", json.dumps(POLICY))
    assert create(client, auth).status_code == 403
    first = publish_policy(POLICY)
    asset = create(client, auth).json()
    publish_policy({**POLICY, "allowed_targets": []})
    publish_policy(POLICY)
    # No intervening reads. New sessions/connections have no process-local authority.
    runtime_engine.dispose()
    assert history(runtime_engine, seeded["org_a"]) == [
        (1, True, "created"),
        (2, False, "policy"),
        (3, True, "policy"),
    ]
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from sqlalchemy import text; "
            "from app.platform.database.session import "
            "get_session_factory,set_organization_context; "
            "from uuid import UUID; "
            "s=get_session_factory()(); "
            f"set_organization_context(s,UUID('{seeded['org_a']}')); "
            "print(s.execute(text('SELECT generation,admitted FROM asset_admission_history "
            "ORDER BY generation')).all()); s.close()",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "[(1, True), (2, False), (3, True)]"
    operator = publisher(publisher_engine, seeded["org_a"])
    result = operator.publish(
        LabPolicy.model_validate_json(json.dumps(POLICY)),
        expected_sequence=0,
        publication_id=UUID(first["revision_id"]),
        provenance="synthetic-fixture",
    )
    assert result["replayed"] and operator.inspect()["sequence"] == 3
    with Session(runtime_engine) as db:
        set_organization_context(db, seeded["org_a"])
        with pytest.raises(ApplicationError, match="generación"):
            require_admission(db, seeded["org_a"], UUID(asset["id"]), generation=1)
        with pytest.raises(ApplicationError, match="admitido"):
            require_admission(db, seeded["org_a"], uuid4())
        assert verify_chain(db, seeded["org_a"]) == (True, None)
    publish_policy({**POLICY, "max_active_assets_per_tenant": 4})
    client.patch(
        f"/api/v1/assets/{asset['id']}",
        headers={**auth["headers"], "If-Match": "1"},
        json={"display_name": "Metadata is not admission"},
    )
    assert len(history(runtime_engine, seeded["org_a"])) == 3
    response = client.request(
        "DELETE",
        f"/api/v1/assets/{asset['id']}",
        headers={**auth["headers"], "If-Match": "2", "Idempotency-Key": "a"},
        json={"reason": "Fixture end"},
    )
    assert response.status_code == 204
    publish_policy(POLICY)
    assert history(runtime_engine, seeded["org_a"])[-1] == (4, False, "archived")


def test_restricted_sql_roles_and_unassigned_tenant(
    publish_policy, publisher_engine, runtime_engine, maintenance_engine, admin_engine, seeded
):
    publish_policy(POLICY)
    with admin_engine.begin() as db:
        db.execute(
            text("DELETE FROM policy_publisher_tenants WHERE organization_id=:org"),
            {"org": seeded["org_b"]},
        )
    with pytest.raises(ValueError, match="not provisioned"):
        publisher(publisher_engine, seeded["org_b"]).inspect()
    for engine in (runtime_engine, maintenance_engine):
        with Session(engine) as db:
            set_organization_context(db, seeded["org_a"])
            denied(
                db,
                "INSERT INTO policy_publisher_tenants VALUES(current_user,:org)",
                {"org": seeded["org_a"]},
            )
            denied(
                db,
                "INSERT INTO lab_policy_revisions(organization_id) VALUES(:org)",
                {"org": seeded["org_a"]},
            )
            denied(db, "UPDATE asset_admission_current SET generation=1")
        with pytest.raises(ValueError, match="restricted"):
            publisher(engine, seeded["org_a"]).inspect()
    with Session(publisher_engine) as db:
        set_organization_context(db, seeded["org_b"])
        assert db.scalar(text("SELECT count(*) FROM lab_policy_revisions")) == 0
        candidate = LabPolicy.model_validate_json(json.dumps(POLICY))
        denied(
            db,
            "INSERT INTO lab_policy_revisions "
            "(organization_id,id,sequence,snapshot,policy_hash,provenance) "
            "VALUES(:org,:id,1,:snapshot,:hash,'unassigned')",
            {
                "org": seeded["org_b"],
                "id": uuid4(),
                "snapshot": candidate.model_dump_json(),
                "hash": candidate.fingerprint,
            },
        )
        for table in ("evidence_operations", "evidence_versions", "sessions", "users"):
            denied(db, f"SELECT * FROM {table}")
        denied(
            db,
            "INSERT INTO policy_publisher_tenants VALUES(current_user,:org)",
            {"org": seeded["org_b"]},
        )
        set_organization_context(db, seeded["org_a"])
        denied(db, "UPDATE lab_policy_revisions SET sequence=99")
        denied(db, "INSERT INTO lab_policy_revisions(published_at) VALUES(now())")
        denied(db, "INSERT INTO lab_policy_revisions(publisher) VALUES('forged')")
        denied(db, "DELETE FROM asset_admission_history")
        denied(db, "SET LOCAL ROLE sentinelai_runtime")


@pytest.mark.parametrize("failure", ["audit", "outbox", "before-commit", "after-commit"])
def test_atomic_publication_and_uncertain_commit(
    publish_policy, publisher_engine, seeded, monkeypatch, failure
):
    operator = publisher(publisher_engine, seeded["org_a"])
    key = uuid4()
    policy = LabPolicy.model_validate_json(json.dumps(POLICY))
    original = operator.commit

    def fail(*args, **kwargs):
        raise OSError("injected publication failure")

    def commit(session):
        if failure == "after-commit":
            session.commit()
        fail()

    with monkeypatch.context() as patch:
        if failure in ("audit", "outbox"):
            patch.setattr(
                publication_module, "record_event" if failure == "audit" else "emit_event", fail
            )
        else:
            patch.setattr(operator, "commit", commit)
        with pytest.raises(OSError, match="injected"):
            operator.publish(policy, expected_sequence=0, publication_id=key, provenance="fixture")
    assert operator.inspect()["sequence"] == (1 if failure == "after-commit" else 0)
    operator.commit = original
    result = operator.publish(policy, expected_sequence=0, publication_id=key, provenance="fixture")
    assert result["replayed"] == (failure == "after-commit")
    assert operator.inspect()["sequence"] == 1
    with pytest.raises(ApplicationError, match="distinta"):
        operator.publish(policy, expected_sequence=0, publication_id=key, provenance="changed")


def test_concurrent_publication_requires_new_review_of_revision(
    publish_policy, publisher_engine, seeded
):
    operator = publisher(publisher_engine, seeded["org_a"])
    policy = LabPolicy.model_validate_json(json.dumps(POLICY))

    def execute(_):
        try:
            return operator.publish(
                policy, expected_sequence=0, publication_id=uuid4(), provenance="fixture"
            )["sequence"]
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(execute, range(2)))
    assert results.count(1) == 1 and results.count("POLICY_REVISION_CONFLICT") == 1


def test_direct_sql_cannot_commit_without_events(publish_policy, publisher_engine, seeded):
    policy = LabPolicy.model_validate_json(json.dumps(POLICY))
    with pytest.raises(DBAPIError) as error, Session(publisher_engine) as db:
        set_organization_context(db, seeded["org_a"])
        db.execute(
            text(
                "INSERT INTO lab_policy_revisions "
                "(organization_id,id,sequence,snapshot,policy_hash,provenance) "
                "VALUES(:org,:id,1,:snapshot,:hash,'fixture')"
            ),
            {
                "org": seeded["org_a"],
                "id": uuid4(),
                "snapshot": policy.model_dump_json(),
                "hash": policy.fingerprint,
            },
        )
        db.commit()
    assert error.value.orig.sqlstate == "23514"
    assert error.value.orig.diag.constraint_name == "policy_events_required"
    assert publisher(publisher_engine, seeded["org_a"]).inspect()["sequence"] == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"version": True},
        {"allowed_targets": ["example.com"]},
        {"allowed_targets": ["127.0.0.1", "127.0.0.1"]},
    ],
)
def test_invalid_candidate_cannot_publish(publish_policy, patch):
    with pytest.raises(ValueError):
        publish_policy({**POLICY, **patch})


def test_cli_inspection_explicit_publish_and_errors(
    publish_policy, publisher_engine, seeded, monkeypatch, capsys
):
    args = ["--organization", str(seeded["org_a"])]
    monkeypatch.delenv("LAB_ASSET_POLICY_JSON", raising=False)
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    monkeypatch.setenv("LAB_ASSET_POLICY_JSON", json.dumps(POLICY))
    monkeypatch.delenv("LAB_POLICY_PUBLISHER_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    monkeypatch.setenv(
        "LAB_POLICY_PUBLISHER_DATABASE_URL",
        publisher_engine.url.render_as_string(hide_password=False),
    )
    main(args)
    assert publisher(publisher_engine, seeded["org_a"]).inspect()["sequence"] == 0
    with pytest.raises(ValueError, match="Explicit"):
        main([*args, "--publish"])
    main(
        [
            *args,
            "--publish",
            "--expected-sequence",
            "0",
            "--publication-id",
            str(uuid4()),
            "--provenance",
            "fixture",
        ]
    )
    assert publisher(publisher_engine, seeded["org_a"]).inspect()["sequence"] == 1
    assert "candidate_hash" in capsys.readouterr().out


@pytest.mark.parametrize(
    "patch",
    [
        {"version": True},
        {"allowed_targets": ["example.com"]},
        {"allowed_targets": ["192.0.2.10", "192.0.2.10"]},
        {"allowed_targets": ["192.000.2.10"]},
        {"excluded_targets": None},
        {"max_active_assets_per_tenant": 0},
        {"authority": True},
    ],
)
def test_sql_publisher_cannot_bypass_contract(publish_policy, publisher_engine, seeded, patch):
    snapshot = json.dumps({**POLICY, **patch}, separators=(",", ":"))
    with Session(publisher_engine) as db:
        set_organization_context(db, seeded["org_a"])
        denied(
            db,
            "INSERT INTO lab_policy_revisions "
            "(organization_id,id,sequence,snapshot,policy_hash,provenance) "
            "VALUES(:org,:id,1,:snapshot,:hash,'invalid-fixture')",
            {
                "org": seeded["org_a"],
                "id": uuid4(),
                "snapshot": snapshot,
                "hash": hashlib.sha256(snapshot.encode()).hexdigest(),
            },
            state="23514",
        )


def test_publication_waits_for_asset_creation_without_missing_admission(
    publish_policy, publisher_engine, client, login, seeded, runtime_engine, monkeypatch
):
    publish_policy(POLICY)
    auth = login()
    created, release, publishing = Event(), Event(), Event()
    original_create = asset_service.AssetRepository.create
    original_lock = publication_module.lock_organization

    def hold_creation(repository, **arguments):
        asset = original_create(repository, **arguments)
        created.set()
        assert release.wait(10)
        return asset

    def publisher_lock(*arguments):
        publishing.set()
        return original_lock(*arguments)

    monkeypatch.setattr(asset_service.AssetRepository, "create", hold_creation)
    monkeypatch.setattr(publication_module, "lock_organization", publisher_lock)
    operator = publisher(publisher_engine, seeded["org_a"])
    with ThreadPoolExecutor(2) as executor:
        asset_future = executor.submit(create, client, auth)
        try:
            assert created.wait(10)
            policy_future = executor.submit(
                operator.publish,
                LabPolicy.model_validate_json(json.dumps({**POLICY, "allowed_targets": []})),
                expected_sequence=1,
                publication_id=uuid4(),
                provenance="racing-with-create",
            )
            assert publishing.wait(10)
            assert not policy_future.done()
        finally:
            release.set()
        assert asset_future.result(timeout=10).status_code == 201
        assert policy_future.result(timeout=10)["sequence"] == 2
    assert history(runtime_engine, seeded["org_a"]) == [(1, True, "created"), (2, False, "policy")]
    assert create(client, auth).status_code == 403


def test_publication_rollback_restores_asset_generation(
    publish_policy, client, login, seeded, runtime_engine, monkeypatch
):
    publish_policy(POLICY)
    assert create(client, login()).status_code == 201

    def failure(*args, **kwargs):
        raise OSError("outbox unavailable")

    monkeypatch.setattr(publication_module, "emit_event", failure)
    with pytest.raises(OSError, match="outbox unavailable"):
        publish_policy({**POLICY, "allowed_targets": []})
    assert history(runtime_engine, seeded["org_a"]) == [(1, True, "created")]
