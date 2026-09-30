"""Add laboratory inventory and HTTP replay without changing delivered revisions."""

import sqlalchemy as sa

from alembic import op

revision = "20260924_05"
down_revision = "20260920_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("type", sa.String(16), nullable=False, server_default="ipv4"),
        sa.Column("canonical_target", sa.String(15), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("criticality", sa.String(16), nullable=False),
        sa.Column("ownership_status", sa.String(16), nullable=False, server_default="unverified"),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("archive_reason", sa.String(500)),
        sa.UniqueConstraint("organization_id", "id", name="uq_assets_organization_id"),
        sa.CheckConstraint(
            "type = 'ipv4' AND ownership_status = 'unverified'", name="ck_assets_kind"
        ),
        sa.CheckConstraint(
            "canonical_target ~ '^([0-9]{1,3}\\.){3}[0-9]{1,3}$' "
            "AND family(canonical_target::inet) = 4 "
            "AND host(canonical_target::inet) = canonical_target",
            name="ck_assets_ipv4",
        ),
        sa.CheckConstraint(
            "criticality IN ('low','medium','high','critical')", name="ck_assets_criticality"
        ),
        sa.CheckConstraint(
            "version > 0 AND length(btrim(display_name)) > 0", name="ck_assets_metadata"
        ),
        sa.CheckConstraint("policy_hash ~ '^[0-9a-f]{64}$'", name="ck_assets_policy_hash"),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND archive_reason IS NULL) OR "
            "(deleted_at IS NOT NULL AND archive_reason IS NOT NULL "
            "AND length(btrim(archive_reason)) > 0)",
            name="ck_assets_archive",
        ),
    )
    op.create_index(
        "uq_assets_active_target",
        "assets",
        ["organization_id", "type", "canonical_target"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_assets_page", "assets", ["organization_id", "created_at", "id"])
    op.create_table(
        "http_idempotency_records",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("route", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "actor_id",
            "method",
            "route",
            "key_hash",
            name="uq_http_idempotency_context",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "asset_id"],
            ["assets.organization_id", "assets.id"],
            name="fk_http_idempotency_asset_tenant",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_http_idempotency_expiry"),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$' AND fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_http_idempotency_hashes",
        ),
        sa.CheckConstraint(
            "(method = 'POST' AND status_code = 201) OR (method = 'DELETE' AND status_code = 204)",
            name="ck_http_idempotency_response",
        ),
    )
    for table in ("assets", "http_idempotency_records"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        expression = "organization_id::text = current_setting('app.organization_id', true)"
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )
    op.execute("""
        CREATE FUNCTION enforce_asset_update() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
          IF ROW(NEW.id, NEW.organization_id, NEW.type, NEW.canonical_target,
                 NEW.ownership_status, NEW.policy_hash, NEW.created_at)
             IS DISTINCT FROM
             ROW(OLD.id, OLD.organization_id, OLD.type, OLD.canonical_target,
                 OLD.ownership_status, OLD.policy_hash, OLD.created_at)
             OR OLD.deleted_at IS NOT NULL OR NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'asset identity/archive is immutable; version must increment'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION enforce_asset_update() FROM PUBLIC;
        CREATE TRIGGER trg_asset_update BEFORE UPDATE ON assets
          FOR EACH ROW EXECUTE FUNCTION enforce_asset_update();
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinelai_runtime') THEN
            GRANT SELECT, INSERT ON assets, http_idempotency_records TO sentinelai_runtime;
            GRANT UPDATE (display_name, criticality, version, updated_at,
                          deleted_at, archive_reason)
              ON assets TO sentinelai_runtime;
            GRANT UPDATE (fingerprint, asset_id, status_code, response_body, created_at, expires_at)
              ON http_idempotency_records TO sentinelai_runtime;
          END IF;
        END $$;
    """)
    bind = op.get_bind()
    for action in ("create", "read", "update", "archive"):
        code = f"asset:{action}"
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, code, resource, action) "
                "VALUES (gen_random_uuid(), :code, 'asset', :action)"
            ),
            {"code": code, "action": action},
        )
        roles = (
            ("org_owner", "security_manager", "analyst", "viewer", "auditor")
            if action == "read"
            else ("org_owner", "security_manager")
        )
        for role in roles:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) "
                    "SELECT r.id, p.id FROM roles r CROSS JOIN permissions p "
                    "WHERE r.organization_id IS NULL AND r.code=:role AND p.code=:code"
                ),
                {"role": role, "code": code},
            )


def downgrade() -> None:
    # Never silently destroy laboratory inventory or replay history.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM assets)")):
        raise RuntimeError("Downgrade revision 05 requires an empty ephemeral asset database.")
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE resource='asset')"
    )
    op.execute("DELETE FROM permissions WHERE resource='asset'")
    op.drop_table("http_idempotency_records")
    op.drop_table("assets")
    op.execute("DROP FUNCTION enforce_asset_update()")
