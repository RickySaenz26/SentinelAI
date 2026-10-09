"""Fresh and populated-04 upgrades must preserve data, schema and permissions."""

import os
import subprocess
from uuid import uuid4

from sqlalchemy import create_engine, text

from tests.test_migration_convergence import (
    BACKEND_ROOT,
    _run_alembic_upgrade,
    _schema_snapshot,
    _temporary_databases,
)


def revision(database_url, command, target):
    result = subprocess.run(
        ["alembic", command, target],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def test_assets_upgrade_from_populated_04_and_reversible_empty_inventory(admin_engine):
    with _temporary_databases(admin_engine.url.render_as_string(hide_password=False)) as urls:
        fresh, existing = urls
        _run_alembic_upgrade(BACKEND_ROOT, fresh)
        result = revision(existing, "upgrade", "20260920_04")
        assert result.returncode == 0, result.stderr
        engine = create_engine(existing)
        identifier = uuid4()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO organizations(id,name,slug) "
                        "VALUES(:id,'Preserved','preserved')"
                    ),
                    {"id": identifier},
                )
            _run_alembic_upgrade(BACKEND_ROOT, existing)
            assert _schema_snapshot(fresh) == _schema_snapshot(existing)
            with engine.connect() as conn:
                assert (
                    conn.scalar(
                        text("SELECT name FROM organizations WHERE id=:id"), {"id": identifier}
                    )
                    == "Preserved"
                )
                permissions = conn.execute(
                    text(
                        "SELECT r.code, p.code FROM role_permissions rp "
                        "JOIN roles r ON r.id=rp.role_id "
                        "JOIN permissions p ON p.id=rp.permission_id "
                        "WHERE p.resource='asset' ORDER BY r.code,p.code"
                    )
                ).all()
                expected = {
                    (role, "asset:read")
                    for role in ("org_owner", "security_manager", "analyst", "viewer", "auditor")
                } | {
                    (role, f"asset:{action}")
                    for role in ("org_owner", "security_manager")
                    for action in ("create", "update", "archive")
                }
                assert set(permissions) == expected
            result = revision(existing, "downgrade", "20260920_04")
            assert result.returncode == 0, result.stderr
            _run_alembic_upgrade(BACKEND_ROOT, existing)
            assert _schema_snapshot(fresh) == _schema_snapshot(existing)
            # Seed historical inventory at 07, before policy publication is mandatory.
            result = revision(existing, "downgrade", "20261006_07")
            assert result.returncode == 0, result.stderr
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO assets (organization_id,canonical_target,display_name,"
                        "criticality,"
                        "policy_hash) VALUES(:org,'192.0.2.10','Preserved inventory','low',:hash)"
                    ),
                    {"org": identifier, "hash": "a" * 64},
                )
            _run_alembic_upgrade(BACKEND_ROOT, existing)
            with engine.connect() as conn:
                before_revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
            refused = revision(existing, "downgrade", "20260920_04")
            assert refused.returncode != 0
            assert "requires an empty ephemeral asset database" in refused.stderr
            with engine.connect() as conn:
                assert conn.scalar(text("SELECT count(*) FROM assets")) == 1
                # PostgreSQL rolls back the whole downgrade, including revisions after 05.
                assert (
                    conn.scalar(text("SELECT version_num FROM alembic_version")) == before_revision
                )
        finally:
            engine.dispose()
