"""Recoverable private evidence references; no verification or scan authority."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_06"
down_revision = "20260924_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sentinelai_evidence_maintenance') THEN
            CREATE ROLE sentinelai_evidence_maintenance NOLOGIN NOSUPERUSER NOCREATEDB
              NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
          IF EXISTS (SELECT FROM pg_roles WHERE rolname='sentinelai_evidence_maintenance'
              AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolcanlogin)) THEN
            RAISE EXCEPTION 'unsafe evidence maintenance role';
          END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO sentinelai_evidence_maintenance;
        ALTER TABLE memberships ADD CONSTRAINT uq_memberships_evidence_identity
          UNIQUE (organization_id, id, user_id);
        CREATE TABLE evidence_maintenance_tenants (
          role_name name NOT NULL,
          organization_id uuid NOT NULL REFERENCES organizations(id),
          PRIMARY KEY (role_name,organization_id)
        );
        ALTER TABLE evidence_maintenance_tenants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE evidence_maintenance_tenants FORCE ROW LEVEL SECURITY;
        CREATE POLICY evidence_maintenance_identity ON evidence_maintenance_tenants
          TO sentinelai_evidence_maintenance USING (role_name=current_user);
        REVOKE ALL ON evidence_maintenance_tenants FROM PUBLIC;
        GRANT SELECT ON evidence_maintenance_tenants TO sentinelai_evidence_maintenance;
        CREATE TABLE evidence_quotas (
          organization_id uuid PRIMARY KEY REFERENCES organizations(id),
          max_objects integer NOT NULL DEFAULT 1000 CHECK (max_objects > 0),
          max_bytes bigint NOT NULL DEFAULT 16777216 CHECK (max_bytes > 0),
          reserved_objects integer NOT NULL DEFAULT 0 CHECK (reserved_objects >= 0),
          reserved_bytes bigint NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0),
          used_objects integer NOT NULL DEFAULT 0 CHECK (used_objects >= 0),
          used_bytes bigint NOT NULL DEFAULT 0 CHECK (used_bytes >= 0),
          CHECK (reserved_objects + used_objects <= max_objects),
          CHECK (reserved_bytes + used_bytes <= max_bytes)
        );
        CREATE TABLE evidence_operations (
          id uuid PRIMARY KEY,
          organization_id uuid NOT NULL REFERENCES evidence_quotas(organization_id),
          asset_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          membership_id uuid NOT NULL,
          session_id uuid NOT NULL,
          asset_version integer NOT NULL CHECK (asset_version > 0),
          version integer NOT NULL CHECK (version > 0),
          policy_hash varchar(64) NOT NULL CHECK (policy_hash ~ '^[0-9a-f]{64}$'),
          key_hash varchar(64) NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
          fingerprint varchar(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
          state varchar(16) NOT NULL DEFAULT 'reserved'
            CHECK (state IN ('reserved','prepared','committed','reclaimed','aborted')),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          expires_at timestamptz NOT NULL,
          object_id uuid UNIQUE,
          envelope_sha256 varchar(64),
          envelope_bytes integer,
          key_id varchar(48),
          wrapped_key varchar(56),
          nonce varchar(16),
          format_version integer,
          FOREIGN KEY (organization_id, asset_id) REFERENCES assets(organization_id,id),
          FOREIGN KEY (organization_id, membership_id, actor_id)
            REFERENCES memberships(organization_id,id,user_id),
          UNIQUE (organization_id,actor_id,key_hash),
          UNIQUE (organization_id,id,asset_id,version,state),
          CHECK (expires_at > created_at),
          CHECK (
            (object_id IS NULL AND envelope_sha256 IS NULL AND envelope_bytes IS NULL
             AND key_id IS NULL AND wrapped_key IS NULL AND nonce IS NULL
             AND format_version IS NULL AND state IN ('reserved','reclaimed','aborted')) OR
            (object_id IS NOT NULL AND envelope_sha256 IS NOT NULL AND envelope_bytes IS NOT NULL
             AND key_id IS NOT NULL AND wrapped_key IS NOT NULL AND nonce IS NOT NULL
             AND format_version IS NOT NULL AND state <> 'reserved'
             AND envelope_sha256 ~ '^[0-9a-f]{64}$' AND envelope_bytes BETWEEN 1 AND 24576
             AND key_id ~ '^[a-z][a-z0-9_-]{0,47}$'
             AND length(decode(wrapped_key,'base64'))=40
             AND length(decode(nonce,'base64'))=12 AND format_version=1)
          )
        );
        CREATE UNIQUE INDEX uq_evidence_active_reservation ON evidence_operations
          (organization_id,asset_id) WHERE state IN ('reserved','prepared','reclaimed');
        CREATE INDEX ix_evidence_expiry ON evidence_operations(organization_id,state,expires_at);
        CREATE TABLE evidence_versions (
          organization_id uuid NOT NULL,
          asset_id uuid NOT NULL,
          version integer NOT NULL,
          operation_id uuid NOT NULL UNIQUE,
          operation_state varchar(16) NOT NULL DEFAULT 'committed'
            CHECK (operation_state='committed'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (organization_id,asset_id,version),
          FOREIGN KEY (organization_id,asset_id) REFERENCES assets(organization_id,id),
          FOREIGN KEY (organization_id,operation_id,asset_id,version,operation_state)
            REFERENCES evidence_operations(organization_id,id,asset_id,version,state)
        );
        CREATE FUNCTION guard_evidence_operation() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.state <> 'reserved' OR NEW.object_id IS NOT NULL THEN
              RAISE EXCEPTION 'operation must begin reserved' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
          END IF;
          IF (to_jsonb(NEW) - ARRAY['state','object_id','envelope_sha256','envelope_bytes',
               'key_id','wrapped_key','nonce','format_version']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['state','object_id','envelope_sha256','envelope_bytes',
               'key_id','wrapped_key','nonce','format_version']) THEN
            RAISE EXCEPTION 'operation identity is immutable' USING ERRCODE='23514';
          END IF;
          IF OLD.state='reserved' AND NEW.state='prepared' THEN
            IF NOT pg_has_role(current_user,'sentinelai_runtime','member') THEN
              RAISE EXCEPTION 'writer role required' USING ERRCODE='42501';
            END IF;
            RETURN NEW;
          END IF;
          IF (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN
            RAISE EXCEPTION 'receipt is immutable' USING ERRCODE='23514';
          END IF;
          IF OLD.state='prepared' AND NEW.state='committed' THEN
            IF NOT pg_has_role(current_user,'sentinelai_runtime','member') THEN
              RAISE EXCEPTION 'writer role required' USING ERRCODE='42501';
            END IF;
            IF NEW.expires_at <= clock_timestamp() THEN
              RAISE EXCEPTION 'reservation expired' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
          END IF;
          IF ((OLD.state IN ('reserved','prepared') AND NEW.state='reclaimed'
                AND OLD.expires_at <= clock_timestamp()) OR
              (OLD.state='reclaimed' AND NEW.state='aborted'))
             AND pg_has_role(current_user,'sentinelai_evidence_maintenance','member') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'invalid operation transition' USING ERRCODE='23514';
        END $$;
        REVOKE ALL ON FUNCTION guard_evidence_operation() FROM PUBLIC;
        CREATE TRIGGER trg_evidence_operation BEFORE INSERT OR UPDATE ON evidence_operations
          FOR EACH ROW EXECUTE FUNCTION guard_evidence_operation();
    """)
    for table in ("evidence_quotas", "evidence_operations", "evidence_versions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        expression = "organization_id::text = current_setting('app.organization_id',true)"
        if op.get_bind().scalar(
            sa.text("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname='sentinelai_runtime')")
        ):
            op.execute(
                f"CREATE POLICY {table}_tenant ON {table} "
                f"TO sentinelai_runtime USING ({expression}) WITH CHECK ({expression})"
            )
        operational = (
            f"{expression} AND EXISTS (SELECT FROM evidence_maintenance_tenants s "
            f"WHERE s.role_name=current_user AND s.organization_id={table}.organization_id)"
        )
        op.execute(
            f"CREATE POLICY {table}_maintenance ON {table} TO sentinelai_evidence_maintenance "
            f"USING ({operational}) WITH CHECK ({operational})"
        )
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON {table} TO sentinelai_evidence_maintenance")
    op.execute("""
        GRANT UPDATE (state) ON evidence_operations TO sentinelai_evidence_maintenance;
        GRANT UPDATE (reserved_objects,reserved_bytes) ON evidence_quotas
          TO sentinelai_evidence_maintenance;
        DO $$ BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname='sentinelai_runtime') THEN
            REVOKE ALL ON evidence_maintenance_tenants FROM sentinelai_runtime;
            REVOKE ALL ON evidence_quotas,evidence_operations,evidence_versions
              FROM sentinelai_runtime;
            GRANT SELECT ON evidence_quotas,evidence_operations,evidence_versions
              TO sentinelai_runtime;
            GRANT INSERT (organization_id) ON evidence_quotas TO sentinelai_runtime;
            GRANT INSERT ON evidence_operations,evidence_versions TO sentinelai_runtime;
            GRANT UPDATE (state,object_id,envelope_sha256,envelope_bytes,key_id,
                          wrapped_key,nonce,format_version) ON evidence_operations
              TO sentinelai_runtime;
            GRANT UPDATE (reserved_objects,reserved_bytes,used_objects,used_bytes)
              ON evidence_quotas TO sentinelai_runtime;
          END IF;
        END $$;
        INSERT INTO permissions(id,code,resource,action)
          VALUES(gen_random_uuid(),'evidence:write','evidence','write');
        INSERT INTO role_permissions(role_id,permission_id)
          SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
          WHERE r.organization_id IS NULL AND r.code IN ('org_owner','security_manager')
            AND p.code='evidence:write';
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT FROM evidence_operations)")):
        raise RuntimeError("Downgrade 06 requires an empty ephemeral evidence database.")
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN
          (SELECT id FROM permissions WHERE resource='evidence');
        DELETE FROM permissions WHERE resource='evidence';
        DROP TABLE evidence_versions,evidence_operations,evidence_quotas;
        DROP TABLE evidence_maintenance_tenants;
        DROP FUNCTION guard_evidence_operation();
        ALTER TABLE memberships DROP CONSTRAINT uq_memberships_evidence_identity;
    """)
    # Cluster-scoped NOLOGIN role may be used by another database; never drop it here.
