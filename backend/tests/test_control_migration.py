"""09 fresh/from populated 08 convergence and conservative downgrade."""

import json
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.assets.policy import LabPolicy
from app.assets.publisher import PolicyPublisher
from tests.test_asset_migration import revision
from tests.test_assets import POLICY
from tests.test_migration_convergence import _schema_snapshot, _temporary_databases


def test_populated_08_upgrade_preserves_inventory_and_authority(admin_engine, publisher_engine):
    with _temporary_databases(admin_engine.url.render_as_string(hide_password=False)) as urls:
        fresh, old = urls
        assert revision(fresh, "upgrade", "head").returncode == 0
        result = revision(old, "upgrade", "20261007_08")
        assert result.returncode == 0, result.stderr
        engine = create_engine(old)
        publisher = create_engine(publisher_engine.url.set(database=engine.url.database))
        try:
            org, asset = uuid4(), uuid4()
            with engine.begin() as db:
                db.execute(
                    text(
                        "INSERT INTO organizations(id,name,slug) "
                        "VALUES(:org,'08 inventory','08-inventory')"
                    ),
                    {"org": org},
                )
                db.execute(
                    text("INSERT INTO policy_publisher_tenants VALUES(:role,:org)"),
                    {"role": publisher_engine.url.username, "org": org},
                )
            policy = LabPolicy.model_validate_json(json.dumps(POLICY))
            PolicyPublisher(sessionmaker(publisher), org).publish(
                policy,
                expected_sequence=0,
                publication_id=uuid4(),
                provenance="populated-08-fixture",
            )
            with engine.begin() as db:
                db.execute(
                    text(
                        "INSERT INTO assets(organization_id,id,canonical_target,display_name,"
                        "criticality,policy_hash) VALUES(:org,:asset,'192.0.2.10',"
                        "'Unadmitted legacy inventory','low',:hash)"
                    ),
                    {"org": org, "asset": asset, "hash": policy.fingerprint},
                )
                before = {
                    table: [dict(r) for r in db.execute(text(f"SELECT * FROM {table}")).mappings()]
                    for table in ("assets", "asset_admission_history", "asset_admission_current")
                }
            result = revision(old, "upgrade", "head")
            assert result.returncode == 0, result.stderr
            assert _schema_snapshot(old) == _schema_snapshot(fresh)
            with engine.connect() as db:
                for table, rows in before.items():
                    actual = [
                        dict(r) for r in db.execute(text(f"SELECT * FROM {table}")).mappings()
                    ]
                    assert actual == rows
                assert db.scalar(text("SELECT ownership_status FROM assets")) == "unverified"
                assert db.scalar(text("SELECT count(*) FROM control_review_requests")) == 0
            result = revision(old, "downgrade", "20261007_08")
            assert result.returncode == 0, result.stderr
            assert revision(old, "upgrade", "head").returncode == 0
            assert _schema_snapshot(old) == _schema_snapshot(fresh)
        finally:
            publisher.dispose()
            engine.dispose()
