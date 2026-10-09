"""Revision 06 upgrades populated 05, converges grants and refuses destructive downgrade."""

from uuid import uuid4

from sqlalchemy import create_engine, text

from tests.test_asset_migration import revision
from tests.test_migration_convergence import (
    BACKEND_ROOT,
    _run_alembic_upgrade,
    _schema_snapshot,
    _temporary_databases,
)


def test_evidence_upgrade_from_populated_05_and_safe_downgrade(admin_engine):
    with _temporary_databases(admin_engine.url.render_as_string(hide_password=False)) as urls:
        fresh, existing = urls
        _run_alembic_upgrade(BACKEND_ROOT, fresh)
        result = revision(existing, "upgrade", "20260924_05")
        assert result.returncode == 0, result.stderr
        engine = create_engine(existing)
        org, asset, user, member = (uuid4() for _ in range(4))
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO organizations(id,name,slug) "
                        "VALUES(:id,'Before 06','before-06')"
                    ),
                    {"id": org},
                )
                connection.execute(
                    text(
                        "INSERT INTO assets(id,organization_id,canonical_target,display_name,"
                        "criticality,policy_hash) "
                        "VALUES(:id,:org,'192.0.2.10','Preserved','low',:hash)"
                    ),
                    {"id": asset, "org": org, "hash": "a" * 64},
                )
            _run_alembic_upgrade(BACKEND_ROOT, existing)
            assert _schema_snapshot(fresh) == _schema_snapshot(existing)
            result = revision(existing, "downgrade", "20260924_05")
            assert result.returncode == 0, result.stderr
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT ownership_status FROM assets WHERE id=:id"), {"id": asset}
                    )
                    == "unverified"
                )
            _run_alembic_upgrade(BACKEND_ROOT, existing)
            assert _schema_snapshot(fresh) == _schema_snapshot(existing)
            # Prepare a genuine pre-08 operation, not a new unbound post-08 operation.
            result = revision(existing, "downgrade", "20261006_07")
            assert result.returncode == 0, result.stderr
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO users(id,email,display_name) "
                        "VALUES(:id,'migration@example.com','Fixture')"
                    ),
                    {"id": user},
                )
                connection.execute(
                    text(
                        "INSERT INTO memberships(id,organization_id,user_id,role_id) "
                        "SELECT :id,:org,:user,id FROM roles WHERE code='org_owner' AND is_system"
                    ),
                    {"id": member, "org": org, "user": user},
                )
                connection.execute(
                    text("INSERT INTO evidence_quotas(organization_id) VALUES(:org)"), {"org": org}
                )
                connection.execute(
                    text(
                        "INSERT INTO evidence_operations(id,organization_id,asset_id,actor_id,"
                        "membership_id,session_id,asset_version,version,policy_hash,"
                        "key_hash,fingerprint,expires_at) "
                        "VALUES(:id,:org,:asset,:user,:member,:session,1,1,:hash,:hash,:hash,"
                        "clock_timestamp()+interval '5 minutes')"
                    ),
                    {
                        "id": uuid4(),
                        "org": org,
                        "asset": asset,
                        "user": user,
                        "member": member,
                        "session": uuid4(),
                        "hash": "b" * 64,
                    },
                )
            _run_alembic_upgrade(BACKEND_ROOT, existing)
            with engine.connect() as connection:
                before_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            result = revision(existing, "downgrade", "20260924_05")
            assert result.returncode != 0
            assert "requires an empty ephemeral evidence database" in result.stderr
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == before_revision
                )
                assert connection.scalar(text("SELECT count(*) FROM evidence_operations")) == 1
        finally:
            engine.dispose()
