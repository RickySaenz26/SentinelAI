"""Populated 07, clean 08, preservation of legacy evidence and safe downgrade."""

import base64
import json
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.assets.authority import current_policy, require_admission
from app.assets.policy import LabPolicy
from app.assets.publisher import PolicyPublisher
from app.core.errors import ApplicationError
from app.platform.database.session import set_organization_context
from tests.test_asset_migration import revision
from tests.test_assets import POLICY
from tests.test_migration_convergence import (
    BACKEND_ROOT,
    _run_alembic_upgrade,
    _schema_snapshot,
    _temporary_databases,
)


def test_populated_07_preserves_evidence_and_downgrade_preserves_publication(
    admin_engine, publisher_engine
):
    with _temporary_databases(admin_engine.url.render_as_string(hide_password=False)) as urls:
        fresh, old = urls
        _run_alembic_upgrade(BACKEND_ROOT, fresh)
        result = revision(old, "upgrade", "20261006_07")
        assert result.returncode == 0, result.stderr
        engine = create_engine(old)
        org, asset, user, member, operation = (uuid4() for _ in range(5))
        operational = None
        try:
            values = {
                "org": org,
                "asset": asset,
                "user": user,
                "member": member,
                "operation": operation,
                "object": uuid4(),
                "hash": "a" * 64,
                "wrapped": base64.b64encode(b"x" * 40).decode(),
                "nonce": base64.b64encode(b"x" * 12).decode(),
            }
            with engine.begin() as db:
                db.execute(
                    text(
                        "INSERT INTO organizations(id,name,slug) "
                        "VALUES(:org,'Legacy fixture','legacy-fixture')"
                    ),
                    values,
                )
                db.execute(
                    text(
                        "INSERT INTO assets(id,organization_id,canonical_target,display_name,"
                        "criticality,policy_hash) VALUES(:asset,:org,'192.0.2.10',"
                        "'Legacy','low',:hash)"
                    ),
                    values,
                )
                db.execute(
                    text(
                        "INSERT INTO users(id,email,display_name) "
                        "VALUES(:user,'legacy@example.com','Fixture')"
                    ),
                    values,
                )
                db.execute(
                    text(
                        "INSERT INTO memberships(id,organization_id,user_id,role_id) "
                        "SELECT :member,:org,:user,id FROM roles "
                        "WHERE code='org_owner' AND is_system"
                    ),
                    values,
                )
                db.execute(
                    text(
                        "INSERT INTO evidence_quotas(organization_id,used_objects,used_bytes) "
                        "VALUES(:org,1,100)"
                    ),
                    values,
                )
                db.execute(
                    text(
                        "INSERT INTO evidence_operations(id,organization_id,asset_id,actor_id,"
                        "membership_id,session_id,asset_version,version,policy_hash,key_hash,"
                        "fingerprint,expires_at) VALUES(:operation,:org,:asset,:user,:member,"
                        "gen_random_uuid(),1,1,:hash,:hash,:hash,"
                        "clock_timestamp()+interval '5 minutes')"
                    ),
                    values,
                )
                db.execute(text("SET LOCAL ROLE sentinelai_runtime"))
                set_organization_context(db, org)
                db.execute(
                    text(
                        "UPDATE evidence_operations SET state='prepared',object_id=:object,"
                        "envelope_sha256=:hash,envelope_bytes=100,key_id='legacy',"
                        "wrapped_key=:wrapped,nonce=:nonce,format_version=1 "
                        "WHERE id=:operation"
                    ),
                    values,
                )
                db.execute(
                    text("UPDATE evidence_operations SET state='committed' WHERE id=:operation"),
                    values,
                )
                db.execute(
                    text(
                        "INSERT INTO evidence_versions(organization_id,asset_id,version,"
                        "operation_id) VALUES(:org,:asset,1,:operation)"
                    ),
                    values,
                )
            with engine.connect() as db:
                before = dict(
                    db.execute(text("SELECT * FROM evidence_operations")).mappings().one()
                )
            _run_alembic_upgrade(BACKEND_ROOT, old)
            assert _schema_snapshot(old) == _schema_snapshot(fresh)
            with engine.connect() as db:
                after = dict(db.execute(text("SELECT * FROM evidence_operations")).mappings().one())
                assert after.pop("admission_generation") is None and before == after
                assert db.scalar(text("SELECT count(*) FROM evidence_versions")) == 1
                assert db.scalar(text("SELECT ownership_status FROM assets")) == "unverified"
                assert db.execute(
                    text("SELECT generation,admitted FROM asset_admission_history")
                ).one() == (0, False)
            runtime = create_engine(
                engine.url.set(username="sentinelai_runtime", password="ephemeral-test-only")
            )
            try:
                with sessionmaker(runtime)() as session:
                    set_organization_context(session, org)
                    assert current_policy(session, org) is None
                    try:
                        require_admission(session, org, asset)
                    except ApplicationError as error:
                        assert error.code == "LAB_POLICY_DENIED"
                    else:
                        raise AssertionError("Upgrade must not activate legacy authority")
                    assert session.scalar(text("SELECT count(*) FROM evidence_versions")) == 1
            finally:
                runtime.dispose()
            result = revision(old, "downgrade", "20261006_07")
            assert result.returncode == 0, result.stderr
            with engine.connect() as db:
                for table in ("security_audit_events", "outbox_events"):
                    assert not db.scalar(
                        text(
                            "SELECT has_any_column_privilege("
                            "'sentinelai_policy_publisher',:table,'INSERT')"
                        ),
                        {"table": table},
                    )
            _run_alembic_upgrade(BACKEND_ROOT, old)
            with engine.begin() as db:
                db.execute(
                    text("INSERT INTO policy_publisher_tenants VALUES(:role,:org)"),
                    {"role": publisher_engine.url.username, "org": org},
                )
            operational = create_engine(publisher_engine.url.set(database=engine.url.database))
            PolicyPublisher(sessionmaker(operational), org).publish(
                LabPolicy.model_validate_json(json.dumps(POLICY)),
                expected_sequence=0,
                publication_id=uuid4(),
                provenance="populated-07-fixture",
            )
            refused = revision(old, "downgrade", "20261006_07")
            assert refused.returncode != 0 and "refuses published policy history" in refused.stderr
            with engine.connect() as db:
                assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261007_08"
                assert db.scalar(text("SELECT count(*) FROM lab_policy_revisions")) == 1
                assert db.scalar(text("SELECT generation FROM asset_admission_current")) == 1
                assert (
                    db.scalar(text("SELECT admission_generation FROM evidence_operations")) is None
                )
        finally:
            if operational is not None:
                operational.dispose()
            engine.dispose()
