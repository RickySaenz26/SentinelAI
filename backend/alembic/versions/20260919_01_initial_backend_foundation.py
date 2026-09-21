"""Initial identity, tenancy, audit, and outbox schema with tenant RLS."""

import sqlalchemy as sa
from alembic import op

revision = "20260919_01"
down_revision = None
branch_labels = None
depends_on = None

SYSTEM_ROLES = {
    "platform_admin": ["platform:admin"],
    "org_owner": [
        "organization:read",
        "organization:update",
        "membership:read",
        "membership:invite",
        "membership:update",
        "membership:revoke",
        "role:read",
        "role:assign",
        "security_audit:read",
    ],
    "security_manager": [
        "organization:read",
        "membership:read",
        "membership:invite",
        "membership:update",
        "membership:revoke",
        "role:read",
        "security_audit:read",
    ],
    "analyst": ["organization:read", "role:read"],
    "viewer": ["organization:read", "role:read"],
    "auditor": ["organization:read", "membership:read", "role:read", "security_audit:read"],
}

PERMISSIONS = (
    "organization:read",
    "organization:update",
    "membership:read",
    "membership:invite",
    "membership:update",
    "membership:revoke",
    "role:read",
    "role:assign",
    "security_audit:read",
    "platform:admin",
)

RLS_TABLES = ("organizations", "roles", "memberships", "security_audit_events", "outbox_events")


def _uuid_column(name: str, *, primary_key: bool = False, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.Uuid(),
        primary_key=primary_key,
        nullable=nullable,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def create_schema() -> None:
    op.create_table(
        "users",
        _uuid_column("id", primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="users_status"),
    )
    op.create_index("uq_users_email_active", "users", ["email"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_table(
        "organizations",
        _uuid_column("id", primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="organizations_status"),
    )
    op.create_index("uq_organizations_slug_active", "organizations", ["slug"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_table(
        "permissions",
        _uuid_column("id", primary_key=True),
        sa.Column("code", sa.String(96), nullable=False, unique=True),
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
    )
    op.create_table(
        "roles",
        _uuid_column("id", primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id")),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "code", name="roles_organization_code"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", sa.Uuid(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("algorithm", sa.String(32), nullable=False, server_default="argon2id"),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "memberships",
        _uuid_column("id", primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="memberships_status"),
    )
    op.create_index("uq_memberships_organization_user_active", "memberships", ["organization_id", "user_id"], unique=True, postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_table(
        "sessions",
        _uuid_column("id", primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("csrf_secret_hash", sa.String(128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent_hash", sa.String(128)),
        sa.Column("ip_prefix_hash", sa.String(128)),
    )
    op.create_table(
        "account_recovery_tokens",
        _uuid_column("id", primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("purpose", sa.String(32), nullable=False, server_default="password_reset"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "security_audit_events",
        _uuid_column("id", primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("prev_hash", sa.String(128)),
        sa.Column("event_hash", sa.String(128), nullable=False),
    )
    op.create_table(
        "outbox_events",
        _uuid_column("id", primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    create_schema()

    for permission in PERMISSIONS:
        resource, action = permission.split(":", maxsplit=1)
        bind.exec_driver_sql(
            """
            INSERT INTO permissions (id, code, resource, action)
            VALUES (gen_random_uuid(), %s, %s, %s)
            ON CONFLICT (code) DO NOTHING
            """,
            (permission, resource, action),
        )

    for role, permissions in SYSTEM_ROLES.items():
        bind.exec_driver_sql(
            """
            INSERT INTO roles (id, organization_id, code, name, is_system, created_at)
            VALUES (gen_random_uuid(), NULL, %s, %s, true, now())
            ON CONFLICT DO NOTHING
            """,
            (role, role.replace("_", " ").title()),
        )
        for permission in permissions:
            bind.exec_driver_sql(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT roles.id, permissions.id FROM roles CROSS JOIN permissions
                WHERE roles.code = %s AND roles.organization_id IS NULL AND permissions.code = %s
                ON CONFLICT DO NOTHING
                """,
                (role, permission),
            )

    for table in RLS_TABLES:
        bind.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        bind.exec_driver_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        if table == "organizations":
            expression = "id::text = current_setting('app.organization_id', true)"
        elif table == "roles":
            expression = "organization_id IS NULL OR organization_id::text = current_setting('app.organization_id', true)"
        else:
            expression = "organization_id::text = current_setting('app.organization_id', true)"
        bind.exec_driver_sql(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )

    bind.exec_driver_sql(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinelai_runtime') THEN
                GRANT USAGE ON SCHEMA public TO sentinelai_runtime;
                GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sentinelai_runtime;
                REVOKE UPDATE, DELETE ON security_audit_events FROM sentinelai_runtime;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sentinelai_runtime;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in RLS_TABLES:
        bind.exec_driver_sql(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    for table in (
        "outbox_events", "security_audit_events", "account_recovery_tokens", "sessions", "memberships",
        "password_credentials", "role_permissions", "roles", "permissions", "organizations", "users",
    ):
        op.drop_table(table)
