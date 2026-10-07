"""Revision 07 changes only permission data; deployed 01-06 stay intact."""

from sqlalchemy import create_engine, text

from tests.test_asset_migration import revision
from tests.test_migration_convergence import _schema_snapshot, _temporary_databases


def test_permission_upgrade_and_downgrade_preserve_schema(admin_engine):
    with _temporary_databases(admin_engine.url.render_as_string(hide_password=False)) as urls:
        url = urls[0]
        assert revision(url, "upgrade", "20261005_06").returncode == 0
        before = _schema_snapshot(url)
        assert revision(url, "upgrade", "head").returncode == 0
        assert _schema_snapshot(url) == before
        engine = create_engine(url)
        query = text(
            "SELECT r.code,p.code FROM roles r JOIN role_permissions rp ON rp.role_id=r.id "
            "JOIN permissions p ON p.id=rp.permission_id WHERE p.resource='evidence'"
        )
        try:
            with engine.connect() as db:
                pairs = set(db.execute(query).all())
            expected = {
                (role, "evidence:summary")
                for role in (
                    "org_owner",
                    "security_manager",
                    "analyst",
                    "auditor",
                    "viewer",
                    "platform_admin",
                )
            }
            expected |= {
                (role, "evidence:write") for role in ("org_owner", "security_manager", "analyst")
            }
            expected |= {
                (role, permission)
                for role in ("org_owner", "security_manager", "auditor")
                for permission in ("evidence:metadata", "evidence:read")
            }
            expected.add(("analyst", "evidence:read_own"))
            assert pairs == expected
            assert revision(url, "downgrade", "20261005_06").returncode == 0
            assert _schema_snapshot(url) == before
            with engine.connect() as db:
                assert set(db.execute(query).all()) == {
                    ("org_owner", "evidence:write"),
                    ("security_manager", "evidence:write"),
                }
        finally:
            engine.dispose()
