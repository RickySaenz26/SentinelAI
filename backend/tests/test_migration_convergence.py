"""Verify that preserved historical migrations converge with a clean upgrade."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LEGACY_REVISION_FILES = (
    "20260919_01_initial_backend_foundation.py",
    "20260919_02_membership_identity_policy.py",
)

SCHEMA_QUERIES = {
    "extensions": """
        SELECT extname, extversion, pg_get_userbyid(extowner)
        FROM pg_extension
        ORDER BY extname
    """,
    "relations": """
        SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
               pg_get_userbyid(c.relowner)
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
        ORDER BY c.relname
    """,
    "columns": """
        SELECT c.relname, a.attnum, a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid),
               a.attidentity, a.attgenerated
        FROM pg_attribute AS a
        JOIN pg_class AS c ON c.oid = a.attrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef AS d
          ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm')
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
    """,
    "constraints": """
        SELECT c.relname, con.conname, con.contype,
               pg_get_constraintdef(con.oid, true), con.convalidated,
               con.condeferrable, con.condeferred
        FROM pg_constraint AS con
        JOIN pg_class AS c ON c.oid = con.conrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY c.relname, con.conname
    """,
    "indexes": """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
    """,
    "triggers": """
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid, true), t.tgenabled
        FROM pg_trigger AS t
        JOIN pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
        ORDER BY c.relname, t.tgname
    """,
    "functions": """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid),
               pg_get_function_result(p.oid), p.prokind, p.prosecdef,
               p.proconfig, pg_get_functiondef(p.oid)
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
        ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)
    """,
    "types": """
        SELECT t.typname, t.typtype, t.typcategory,
               pg_catalog.format_type(t.oid, NULL), pg_get_userbyid(t.typowner)
        FROM pg_type AS t
        JOIN pg_namespace AS n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype IN ('d', 'e')
        ORDER BY t.typname
    """,
    "policies": """
        SELECT c.relname, p.polname, p.polpermissive, p.polcmd,
               ARRAY(
                   SELECT CASE WHEN role_oid = 0 THEN 'PUBLIC'
                               ELSE pg_get_userbyid(role_oid) END
                   FROM unnest(p.polroles) AS role_oid
                   ORDER BY 1
               ),
               pg_get_expr(p.polqual, p.polrelid),
               pg_get_expr(p.polwithcheck, p.polrelid)
        FROM pg_policy AS p
        JOIN pg_class AS c ON c.oid = p.polrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY c.relname, p.polname
    """,
    "relation_acl": """
        SELECT c.relname,
               CASE WHEN acl.grantor = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantor) END,
               CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantee) END,
               acl.privilege_type, acl.is_grantable
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(
                c.relacl,
                acldefault(CASE WHEN c.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END,
                           c.relowner)
            )
        ) AS acl
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
        ORDER BY c.relname, 2, 3, 4, 5
    """,
    "column_acl": """
        SELECT c.relname, a.attname,
               CASE WHEN acl.grantor = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantor) END,
               CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantee) END,
               acl.privilege_type, acl.is_grantable
        FROM pg_attribute AS a
        JOIN pg_class AS c ON c.oid = a.attrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(a.attacl) AS acl
        WHERE n.nspname = 'public'
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY c.relname, a.attname, 3, 4, 5, 6
    """,
    "function_acl": """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid),
               CASE WHEN acl.grantor = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantor) END,
               CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantee) END,
               acl.privilege_type, acl.is_grantable
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(p.proacl, acldefault('f'::"char", p.proowner))
        ) AS acl
        WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
        ORDER BY p.proname, pg_get_function_identity_arguments(p.oid), 3, 4, 5, 6
    """,
    "schema_acl": """
        SELECT n.nspname,
               CASE WHEN acl.grantor = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantor) END,
               CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantee) END,
               acl.privilege_type, acl.is_grantable
        FROM pg_namespace AS n
        CROSS JOIN LATERAL aclexplode(
            COALESCE(n.nspacl, acldefault('n'::"char", n.nspowner))
        ) AS acl
        WHERE n.nspname = 'public'
        ORDER BY 1, 2, 3, 4, 5
    """,
    "default_acl": """
        SELECT pg_get_userbyid(d.defaclrole), COALESCE(n.nspname, ''), d.defaclobjtype,
               CASE WHEN acl.grantor = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantor) END,
               CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                    ELSE pg_get_userbyid(acl.grantee) END,
               acl.privilege_type, acl.is_grantable
        FROM pg_default_acl AS d
        LEFT JOIN pg_namespace AS n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) AS acl
        ORDER BY 1, 2, 3, 4, 5, 6, 7
    """,
}


def _database_url(base_url: str, database_name: str) -> str:
    return make_url(base_url).set(database=database_name).render_as_string(hide_password=False)


@contextmanager
def _temporary_databases(base_url: str) -> Iterator[tuple[str, str]]:
    database_names = (
        f"closure_test_current_{uuid4().hex}",
        f"closure_test_legacy_{uuid4().hex}",
    )
    maintenance_url = _database_url(base_url, "postgres")
    maintenance_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        with maintenance_engine.connect() as connection:
            for database_name in database_names:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        yield tuple(_database_url(base_url, name) for name in database_names)
    finally:
        with maintenance_engine.connect() as connection:
            for database_name in database_names:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        maintenance_engine.dispose()


def _run_alembic_upgrade(project_root: Path, database_url: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(project_root), environment.get("PYTHONPATH")) if value
    )
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    failure = (
        f"Alembic upgrade failed in {project_root}.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert result.returncode == 0, failure


def _create_legacy_project(tmp_path: Path) -> Path:
    legacy_root = tmp_path / "legacy_backend"
    shutil.copytree(BACKEND_ROOT / "app", legacy_root / "app")
    shutil.copy2(BACKEND_ROOT / "alembic.ini", legacy_root / "alembic.ini")
    (legacy_root / "alembic" / "versions").mkdir(parents=True)
    shutil.copy2(BACKEND_ROOT / "alembic" / "env.py", legacy_root / "alembic" / "env.py")
    shutil.copy2(
        BACKEND_ROOT / "alembic" / "script.py.mako",
        legacy_root / "alembic" / "script.py.mako",
    )
    shutil.copy2(
        BACKEND_ROOT / "tests" / "legacy_models.py.snapshot",
        legacy_root / "app" / "platform" / "database" / "models.py",
    )
    for revision_file in LEGACY_REVISION_FILES:
        shutil.copy2(
            BACKEND_ROOT / "tests" / f"legacy_{revision_file}.snapshot",
            legacy_root / "alembic" / "versions" / revision_file,
        )
    return legacy_root


def _normalize(value: object) -> object:
    if isinstance(value, list):
        return tuple(_normalize(item) for item in value)
    return value


def _schema_snapshot(database_url: str) -> dict[str, tuple[tuple[object, ...], ...]]:
    engine: Engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                name: tuple(
                    tuple(_normalize(value) for value in row)
                    for row in connection.execute(text(query))
                )
                for name, query in SCHEMA_QUERIES.items()
            }
    finally:
        engine.dispose()


def test_historical_migrations_converge_with_clean_current_schema(
    admin_engine: Engine, tmp_path: Path
) -> None:
    """Historical 01/02 plus current 03/04 must equal a clean 01-to-head schema."""
    base_url = admin_engine.url.render_as_string(hide_password=False)
    legacy_root = _create_legacy_project(tmp_path)

    with _temporary_databases(base_url) as (current_url, legacy_url):
        _run_alembic_upgrade(BACKEND_ROOT, current_url)
        _run_alembic_upgrade(legacy_root, legacy_url)
        _run_alembic_upgrade(BACKEND_ROOT, legacy_url)

        assert _schema_snapshot(legacy_url) == _schema_snapshot(current_url)
