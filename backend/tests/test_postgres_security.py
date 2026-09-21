"""Positive and adversarial SQL under the real, least-privileged runtime role."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.platform.database.models import Role
from app.platform.database.session import set_organization_context, set_user_context


def denied(db, statement, parameters=None, *, state="42501"):
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            db.execute(text(statement), parameters or {})
    assert error.value.orig.sqlstate == state, str(error.value.orig)


def insert_session(db, seeded, **overrides):
    values = {
        "id": uuid4(), "user": seeded["users"]["viewer"], "org": seeded["org_a"],
        "member": seeded["memberships"]["viewer"], "hash": uuid4().hex * 2,
    } | overrides
    db.execute(text(
        "INSERT INTO sessions (id,user_id,organization_id,membership_id,token_hash,"
        "csrf_secret_hash,expires_at,idle_expires_at) VALUES "
        "(:id,:user,:org,:member,:hash,:hash,now()+interval '1 hour',now()+interval '30 minutes')"
    ), values)
    return values["id"]


def test_rls_without_context_hides_all_tenant_rows(db, seeded):
    for table in ("organizations", "memberships", "security_audit_events", "outbox_events"):
        assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    # System role lookup is intentionally global, while tenant data remains hidden.
    assert db.scalar(text("SELECT count(*) FROM roles WHERE organization_id IS NULL")) == 6
    denied(db, "INSERT INTO memberships(organization_id,user_id,role_id) VALUES (:org,:user,:role)",
        {"org": seeded["org_b"], "user": seeded["users"]["viewer"],
         "role": seeded["roles"]["viewer"]})


def test_rls_correct_context_and_cross_tenant_queries(db, seeded):
    set_organization_context(db, seeded["org_a"])
    assert db.scalar(text("SELECT count(*) FROM memberships")) == 6
    assert db.scalar(text("SELECT count(*) FROM organizations")) == 1
    assert db.scalar(text("SELECT count(*) FROM memberships WHERE id=:id"),
                     {"id": seeded["owner_b_membership"]}) == 0
    assert db.execute(text("UPDATE memberships SET status='suspended' WHERE id=:id"),
                      {"id": seeded["owner_b_membership"]}).rowcount == 0
    assert db.execute(text("UPDATE organizations SET name='Hidden' WHERE id=:id"),
                      {"id": seeded["org_b"]}).rowcount == 0
    set_organization_context(db, seeded["org_b"])
    assert db.scalar(text("SELECT count(*) FROM memberships")) == 1
    assert db.scalar(text("SELECT id FROM organizations")) == seeded["org_b"]


def test_user_context_only_resolves_own_memberships_and_cannot_write(db, seeded):
    set_user_context(db, seeded["users"]["org_owner"])
    assert db.scalar(text("SELECT count(*) FROM memberships")) == 2
    assert db.scalar(text("SELECT count(*) FROM organizations")) == 0
    assert db.execute(text("UPDATE memberships SET status='suspended' WHERE id=:id"),
                      {"id": seeded["memberships"]["viewer"]}).rowcount == 0
    denied(db, "INSERT INTO memberships(organization_id,user_id,role_id) VALUES (:org,:user,:role)",
        {"org": seeded["org_b"], "user": seeded["users"]["analyst"],
         "role": seeded["roles"]["viewer"]})


def test_runtime_identity_has_no_privileged_database_authority(db):
    role = db.execute(text(
        "SELECT rolname,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles "
        "WHERE rolname=current_user"
    )).one()
    assert role.rolname == "sentinelai_runtime"
    assert not any((role.rolsuper, role.rolbypassrls, role.rolcreaterole, role.rolcreatedb))
    assert not db.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')"))
    assert db.scalar(text(
        "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND r.rolname=current_user"
    )) == 0
    denied(db, "CREATE TABLE public.runtime_must_not_create(id integer)")
    denied(db, "SET LOCAL ROLE sentinelai_migrator")


@pytest.mark.parametrize("table,insert_allowed,update_allowed", [
    ("users", True, False), ("password_credentials", True, False),
    ("sessions", True, False), ("account_recovery_tokens", True, False),
    ("organizations", True, True), ("memberships", True, True),
    ("roles", False, False), ("permissions", False, False),
    ("role_permissions", False, False), ("security_audit_events", True, False),
    ("outbox_events", True, False),
])
def test_table_privilege_matrix(db, table, insert_allowed, update_allowed):
    grants = db.execute(text(
        "SELECT has_table_privilege(current_user,:table,'SELECT') AS read, "
        "has_table_privilege(current_user,:table,'INSERT') AS insert, "
        "has_table_privilege(current_user,:table,'UPDATE') AS update, "
        "has_table_privilege(current_user,:table,'DELETE') AS delete"
    ), {"table": table}).one()
    assert grants.read is True
    assert grants.insert is insert_allowed
    assert grants.update is update_allowed
    assert grants.delete is False
    assert db.execute(text(f"SELECT * FROM {table} LIMIT 0")).returns_rows
    denied(db, f"DELETE FROM {table}")


@pytest.mark.parametrize("table,allowed", [
    ("sessions", {"revoked_at", "last_seen_at", "idle_expires_at"}),
    ("password_credentials", {"password_hash", "changed_at"}),
    ("account_recovery_tokens", {"consumed_at"}),
])
def test_update_column_allowlists_are_exact(db, table, allowed):
    columns = db.execute(text(
        "SELECT attname,has_column_privilege(current_user,:table,attname,'UPDATE') AS permitted "
        "FROM pg_attribute WHERE attrelid=CAST(:table AS regclass) AND attnum>0 AND NOT attisdropped"
    ), {"table": table}).all()
    assert {row.attname for row in columns if row.permitted} == allowed
    for row in columns:
        if row.attname not in allowed:
            denied(db, f"UPDATE {table} SET {row.attname}={row.attname}")


def test_runtime_can_insert_and_update_only_operational_session_fields(db, seeded):
    set_organization_context(db, seeded["org_a"])
    session_id = insert_session(db, seeded)
    assert db.execute(text(
        "UPDATE sessions SET last_seen_at=now(),idle_expires_at=now()+interval '10 minutes',"
        "revoked_at=now() WHERE id=:id"
    ), {"id": session_id}).rowcount == 1
    assert db.scalar(text("SELECT revoked_at FROM sessions WHERE id=:id"), {"id": session_id})


def test_runtime_can_update_password_and_consume_recovery_but_not_identity(db, seeded):
    assert db.execute(text(
        "UPDATE password_credentials SET password_hash=password_hash,changed_at=now() "
        "WHERE user_id=:user"
    ), {"user": seeded["users"]["viewer"]}).rowcount == 1
    recovery_id = uuid4()
    db.execute(text(
        "INSERT INTO account_recovery_tokens(id,user_id,token_hash,expires_at) "
        "VALUES(:id,:user,:hash,now()+interval '30 minutes')"
    ), {"id": recovery_id, "user": seeded["users"]["viewer"], "hash": uuid4().hex * 2})
    assert db.execute(text("UPDATE account_recovery_tokens SET consumed_at=now() WHERE id=:id"),
                      {"id": recovery_id}).rowcount == 1
    denied(db, "UPDATE users SET display_name='Unauthorized' WHERE id=:id",
           {"id": seeded["users"]["viewer"]})


def test_runtime_cannot_rewrite_roles_permissions_audit_or_outbox(db, seeded):
    set_organization_context(db, seeded["org_a"])
    for table, column in (("roles", "name"), ("permissions", "resource"),
                          ("role_permissions", "role_id"), ("security_audit_events", "details"),
                          ("outbox_events", "payload")):
        denied(db, f"UPDATE {table} SET {column}={column}")
    denied(db, "INSERT INTO roles(code,name,is_system) VALUES('future_admin','Not allowed',true)")
    denied(db, "INSERT INTO permissions(code,resource,action) VALUES('bad:permission','bad','permission')")
    denied(db, "INSERT INTO role_permissions(role_id,permission_id) SELECT role_id,permission_id FROM role_permissions LIMIT 1")


def test_runtime_platform_insert_cannot_be_enabled_with_forged_application_flags(
    db, seeded, create_user
):
    user = create_user(role=None, organization_id=seeded["org_a"])
    set_organization_context(db, seeded["org_a"])
    db.execute(text("SELECT set_config('app.bootstrap','true',true)"))
    db.execute(text("SELECT set_config('app.platform_admin','true',true)"))
    denied(db, "INSERT INTO memberships(organization_id,user_id,role_id) VALUES(:org,:user,:role)",
        {"org": seeded["org_a"], "user": user["id"], "role": seeded["roles"]["platform_admin"]},
        state="23514")
    assert db.scalar(text("SELECT count(*) FROM memberships WHERE role_id=:role"),
                     {"role": seeded["roles"]["platform_admin"]}) == 1


@pytest.mark.parametrize("field", ["role_id", "status", "organization_id", "deleted_at", "user_id"])
def test_runtime_platform_membership_is_immutable(db, seeded, field):
    set_organization_context(db, seeded["org_a"])
    values = {"role_id": seeded["roles"]["viewer"], "status": "suspended",
              "organization_id": seeded["org_b"], "deleted_at": datetime.now(UTC),
              "user_id": seeded["users"]["viewer"]}
    denied(db, f"UPDATE memberships SET {field}=:value WHERE id=:id",
        {"value": values[field], "id": seeded["memberships"]["platform_admin"]}, state="23514")


def test_runtime_cannot_promote_existing_membership_or_delete_platform_membership(db, seeded):
    set_organization_context(db, seeded["org_a"])
    denied(db, "UPDATE memberships SET role_id=:role WHERE id=:id",
        {"role": seeded["roles"]["platform_admin"], "id": seeded["memberships"]["viewer"]},
        state="23514")
    denied(db, "DELETE FROM memberships WHERE id=:id", {"id": seeded["memberships"]["platform_admin"]})


@pytest.mark.parametrize("inconsistency", ["member", "user", "organization", "null", "context"])
def test_runtime_session_insert_enforces_principal_and_tenant_binding(db, seeded, inconsistency):
    if inconsistency != "context":
        set_organization_context(db, seeded["org_a"])
    changes = {
        "member": {"member": seeded["memberships"]["org_owner"]},
        "user": {"user": seeded["users"]["org_owner"]},
        "organization": {"org": seeded["org_b"]},
        "null": {"member": None}, "context": {},
    }
    with pytest.raises(DBAPIError) as error:
        with db.begin_nested():
            insert_session(db, seeded, **changes[inconsistency])
    assert error.value.orig.sqlstate == "23514"
    assert db.scalar(text("SELECT count(*) FROM sessions")) == 0


def test_runtime_tenant_role_of_other_organization_is_rejected(db, admin_engine, seeded, create_user):
    with Session(admin_engine, expire_on_commit=False) as admin:
        role = Role(organization_id=seeded["org_b"], code="custom_tenant", name="Tenant only", is_system=False)
        admin.add(role)
        admin.commit()
        role_id = role.id
    user = create_user(role=None, organization_id=seeded["org_a"])
    set_organization_context(db, seeded["org_a"])
    assert db.scalar(text("SELECT count(*) FROM roles WHERE id=:id"), {"id": role_id}) == 0
    denied(db, "INSERT INTO memberships(organization_id,user_id,role_id) VALUES(:org,:user,:role)",
        {"org": seeded["org_a"], "user": user["id"], "role": role_id}, state="23514")
    denied(db, "UPDATE memberships SET role_id=:role WHERE id=:id",
        {"role": role_id, "id": seeded["memberships"]["viewer"]}, state="23514")


def test_all_tenant_tables_force_rls_and_runtime_cannot_call_definer_functions(db):
    rows = db.execute(text(
        "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('organizations','roles','memberships','security_audit_events','outbox_events')"
    )).all()
    assert len(rows) == 5
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
    for function in ("enforce_membership_role_tenant()", "enforce_session_membership()"):
        assert not db.scalar(text("SELECT has_function_privilege(current_user,:function,'EXECUTE')"),
                             {"function": function})
