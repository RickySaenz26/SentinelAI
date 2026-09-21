"""Remediate tenant privilege escalation, RLS, grants, and relational integrity."""

from alembic import op

revision = "20260919_03"
down_revision = "20260919_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS membership_id uuid")
    op.execute(
        """
        UPDATE sessions AS s SET membership_id = m.id
        FROM memberships AS m
        WHERE m.user_id = s.user_id AND m.organization_id = s.organization_id
          AND m.status = 'active' AND m.deleted_at IS NULL
        """
    )
    op.execute("UPDATE sessions SET revoked_at = now() WHERE membership_id IS NULL AND revoked_at IS NULL")
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_membership_id "
        "FOREIGN KEY (membership_id) REFERENCES memberships(id) ON DELETE RESTRICT"
    )
    op.execute("ALTER TABLE security_audit_events ADD COLUMN IF NOT EXISTS actor_type varchar(32) NOT NULL DEFAULT 'user'")
    op.execute("ALTER TABLE outbox_events ADD COLUMN IF NOT EXISTS idempotency_key varchar(128)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_roles_global_code ON roles(code) WHERE organization_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_roles_tenant_code ON roles(organization_id, code) "
        "WHERE organization_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_organization_idempotency_key "
        "ON outbox_events(organization_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox_events(organization_id, published_at, created_at)")
    op.execute(
        "ALTER TABLE roles ADD CONSTRAINT ck_roles_system_scope "
        "CHECK ((is_system AND organization_id IS NULL) OR ((NOT is_system) AND organization_id IS NOT NULL))"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_membership_role_tenant() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE role_organization_id uuid;
        BEGIN
          SELECT organization_id INTO role_organization_id FROM roles WHERE id = NEW.role_id;
          IF NOT FOUND OR (role_organization_id IS NOT NULL AND role_organization_id <> NEW.organization_id) THEN
            RAISE EXCEPTION 'membership role does not belong to organization' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_membership_role_tenant BEFORE INSERT OR UPDATE OF organization_id, role_id
          ON memberships FOR EACH ROW EXECUTE FUNCTION enforce_membership_role_tenant();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_session_membership() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM memberships m WHERE m.id = NEW.membership_id AND m.user_id = NEW.user_id
              AND m.organization_id = NEW.organization_id AND m.status = 'active' AND m.deleted_at IS NULL
          ) THEN
            RAISE EXCEPTION 'session membership is inconsistent' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_session_membership BEFORE INSERT OR UPDATE OF user_id, organization_id, membership_id
          ON sessions FOR EACH ROW EXECUTE FUNCTION enforce_session_membership();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION revoke_sessions_when_principal_inactive() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = 'users' AND (NEW.status <> 'active' OR NEW.deleted_at IS NOT NULL) THEN
            UPDATE sessions SET revoked_at = now() WHERE user_id = NEW.id AND revoked_at IS NULL;
          ELSIF TG_TABLE_NAME = 'organizations' AND (NEW.status <> 'active' OR NEW.deleted_at IS NOT NULL) THEN
            UPDATE sessions SET revoked_at = now() WHERE organization_id = NEW.id AND revoked_at IS NULL;
          ELSIF TG_TABLE_NAME = 'memberships' AND (NEW.status <> 'active' OR NEW.deleted_at IS NOT NULL) THEN
            UPDATE sessions SET revoked_at = now() WHERE membership_id = NEW.id AND revoked_at IS NULL;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_revoke_user_sessions AFTER UPDATE OF status, deleted_at ON users
          FOR EACH ROW EXECUTE FUNCTION revoke_sessions_when_principal_inactive();
        CREATE TRIGGER trg_revoke_organization_sessions AFTER UPDATE OF status, deleted_at ON organizations
          FOR EACH ROW EXECUTE FUNCTION revoke_sessions_when_principal_inactive();
        CREATE TRIGGER trg_revoke_membership_sessions AFTER UPDATE OF status, deleted_at ON memberships
          FOR EACH ROW EXECUTE FUNCTION revoke_sessions_when_principal_inactive();
        """
    )
    for table in ("organizations", "roles", "memberships", "security_audit_events", "outbox_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        "CREATE POLICY organizations_select ON organizations FOR SELECT USING "
        "(id::text = current_setting('app.organization_id', true))"
    )
    op.execute(
        "CREATE POLICY organizations_write ON organizations FOR ALL USING "
        "(id::text = current_setting('app.organization_id', true)) WITH CHECK "
        "(id::text = current_setting('app.organization_id', true))"
    )
    op.execute(
        "CREATE POLICY roles_select ON roles FOR SELECT USING "
        "(organization_id IS NULL OR organization_id::text = current_setting('app.organization_id', true))"
    )
    op.execute(
        "CREATE POLICY memberships_select ON memberships FOR SELECT USING "
        "(organization_id::text = current_setting('app.organization_id', true) "
        "OR user_id::text = current_setting('app.user_id', true))"
    )
    op.execute(
        "CREATE POLICY memberships_insert ON memberships FOR INSERT WITH CHECK "
        "(organization_id::text = current_setting('app.organization_id', true))"
    )
    op.execute(
        "CREATE POLICY memberships_update ON memberships FOR UPDATE USING "
        "(organization_id::text = current_setting('app.organization_id', true)) WITH CHECK "
        "(organization_id::text = current_setting('app.organization_id', true))"
    )
    for table in ("security_audit_events", "outbox_events"):
        op.execute(
            f"CREATE POLICY {table}_select ON {table} FOR SELECT USING "
            "(organization_id::text = current_setting('app.organization_id', true))"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK "
            "(organization_id::text = current_setting('app.organization_id', true))"
        )
    op.execute(
        "CREATE POLICY outbox_events_update ON outbox_events FOR UPDATE USING "
        "(organization_id::text = current_setting('app.organization_id', true)) WITH CHECK "
        "(organization_id::text = current_setting('app.organization_id', true))"
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinelai_runtime') THEN
            REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sentinelai_runtime;
            REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM sentinelai_runtime;
            GRANT USAGE ON SCHEMA public TO sentinelai_runtime;
            GRANT SELECT, INSERT, UPDATE ON users, password_credentials, memberships, sessions,
              account_recovery_tokens, organizations TO sentinelai_runtime;
            GRANT SELECT ON permissions, roles, role_permissions TO sentinelai_runtime;
            GRANT SELECT, INSERT ON security_audit_events TO sentinelai_runtime;
            GRANT SELECT, INSERT, UPDATE ON outbox_events TO sentinelai_runtime;
            ALTER DEFAULT PRIVILEGES FOR ROLE sentinelai_migrator IN SCHEMA public
              REVOKE ALL ON TABLES FROM sentinelai_runtime;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for policy, table in (
        ("organizations_select", "organizations"),
        ("organizations_write", "organizations"),
        ("roles_select", "roles"),
        ("memberships_select", "memberships"),
        ("memberships_insert", "memberships"),
        ("memberships_update", "memberships"),
        ("security_audit_events_select", "security_audit_events"),
        ("security_audit_events_insert", "security_audit_events"),
        ("outbox_events_select", "outbox_events"),
        ("outbox_events_insert", "outbox_events"),
        ("outbox_events_update", "outbox_events"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute("DROP TRIGGER IF EXISTS trg_revoke_membership_sessions ON memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_revoke_organization_sessions ON organizations")
    op.execute("DROP TRIGGER IF EXISTS trg_revoke_user_sessions ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_session_membership ON sessions")
    op.execute("DROP TRIGGER IF EXISTS trg_membership_role_tenant ON memberships")
    op.execute("DROP FUNCTION IF EXISTS revoke_sessions_when_principal_inactive()")
    op.execute("DROP FUNCTION IF EXISTS enforce_session_membership()")
    op.execute("DROP FUNCTION IF EXISTS enforce_membership_role_tenant()")
    op.execute("DROP INDEX IF EXISTS ix_outbox_pending")
    op.execute("DROP INDEX IF EXISTS uq_outbox_organization_idempotency_key")
    op.execute("DROP INDEX IF EXISTS uq_roles_tenant_code")
    op.execute("DROP INDEX IF EXISTS uq_roles_global_code")
    op.execute("ALTER TABLE roles DROP CONSTRAINT IF EXISTS ck_roles_system_scope")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_membership_id")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS membership_id")
    op.execute("ALTER TABLE security_audit_events DROP COLUMN IF EXISTS actor_type")
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS idempotency_key")
