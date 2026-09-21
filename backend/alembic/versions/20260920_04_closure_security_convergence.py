"""Converge delivered schemas without rewriting prior revisions or audit history."""

import hashlib
import json
from collections import defaultdict
from datetime import UTC

import sqlalchemy as sa

from alembic import op

revision = "20260920_04"
down_revision = "20260919_03"
branch_labels = None
depends_on = None


def _legacy_digest(row, version):
    if version == 1:
        material = {
            "action": row["action"],
            "outcome": row["outcome"],
            "previous": row["prev_hash"],
            "request_id": row["request_id"],
        }
    else:
        material = {
            "action": row["action"],
            "actor_type": row["actor_type"],
            "actor_user_id": str(row["actor_user_id"]) if row["actor_user_id"] else None,
            "details": row["details"],
            "event_id": str(row["id"]),
            "occurred_at": row["occurred_at"].astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "organization_id": str(row["organization_id"]),
            "outcome": row["outcome"],
            "previous_hash": row["prev_hash"],
            "request_id": row["request_id"],
            "resource_id": str(row["resource_id"]) if row["resource_id"] else None,
            "resource_type": row["resource_type"],
        }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _backfill_audit_chain():
    """Follow stored links, preserving original hashes and explicitly tagging their format."""
    bind = op.get_bind()
    by_organization = defaultdict(list)
    for row in bind.execute(sa.text("SELECT * FROM security_audit_events")).mappings():
        by_organization[row["organization_id"]].append(row)
    for organization_id, rows in by_organization.items():
        successors = defaultdict(list)
        for row in rows:
            successors[row["prev_hash"]].append(row)
        previous = None
        visited = set()
        for sequence in range(1, len(rows) + 1):
            candidates = successors.get(previous, [])
            if len(candidates) != 1 or candidates[0]["id"] in visited:
                raise RuntimeError(
                    f"Audit history for organization {organization_id} has a fork, cycle or "
                    "missing link. Preserve the records and investigate before retrying "
                    "revision 04."
                )
            row = candidates[0]
            version = next(
                (value for value in (2, 1) if _legacy_digest(row, value) == row["event_hash"]),
                None,
            )
            if version is None:
                raise RuntimeError(
                    f"Audit event {row['id']} has an unrecognized or altered hash. "
                    "Preserve the record and investigate before retrying revision 04."
                )
            bind.execute(
                sa.text(
                    "UPDATE security_audit_events SET sequence=:sequence, hash_version=:version "
                    "WHERE id=:id"
                ),
                {"sequence": sequence, "version": version, "id": row["id"]},
            )
            visited.add(row["id"])
            previous = row["event_hash"]


def _converge_defaults():
    uuid_tables = (
        "users",
        "organizations",
        "permissions",
        "roles",
        "memberships",
        "sessions",
        "account_recovery_tokens",
        "security_audit_events",
        "outbox_events",
    )
    for table in uuid_tables:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT gen_random_uuid()")
    timestamps = {
        "users": ("created_at", "updated_at"),
        "organizations": ("created_at", "updated_at"),
        "roles": ("created_at",),
        "password_credentials": ("changed_at",),
        "memberships": ("created_at", "updated_at"),
        "sessions": ("issued_at", "last_seen_at"),
        "account_recovery_tokens": ("created_at",),
        "security_audit_events": ("occurred_at",),
        "outbox_events": ("created_at",),
    }
    for table, columns in timestamps.items():
        for column in columns:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()")
    defaults = {
        "users": {"status": "'active'", "version": "1"},
        "organizations": {"status": "'active'", "version": "1"},
        "memberships": {"status": "'active'", "version": "1"},
        "roles": {"is_system": "true"},
        "password_credentials": {"algorithm": "'argon2id'", "parameters": "'{}'::json"},
        "account_recovery_tokens": {"purpose": "'password_reset'"},
        "security_audit_events": {"details": "'{}'::json", "actor_type": "'user'"},
        "outbox_events": {"payload": "'{}'::json", "attempts": "0"},
    }
    for table, columns in defaults.items():
        for column, default in columns.items():
            op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}")


def upgrade() -> None:
    bind = op.get_bind()
    # No row deletion or inferred tenant assignment is safe for historical global events.
    for table in ("security_audit_events", "outbox_events"):
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table} WHERE organization_id IS NULL")):
            raise RuntimeError(
                f"{table}.organization_id contains NULL historical data. Identify the correct "
                "organization using retained evidence and explicitly repair it before revision 04; "
                "no records have been removed."
            )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN organization_id SET NOT NULL")
    invalid_sessions = bind.scalar(
        sa.text(
            "SELECT count(*) FROM sessions s LEFT JOIN memberships m ON m.id=s.membership_id "
            "WHERE (s.membership_id IS NULL AND s.revoked_at IS NULL) OR "
            "(s.membership_id IS NOT NULL AND (m.id IS NULL OR m.user_id<>s.user_id "
            "OR m.organization_id<>s.organization_id))"
        )
    )
    if invalid_sessions:
        raise RuntimeError(
            "Historical sessions have inconsistent membership bindings. Investigate and revoke "
            "or repair affected sessions explicitly before retrying revision 04."
        )
    _converge_defaults()
    op.execute("ALTER TABLE security_audit_events ADD COLUMN sequence bigint")
    op.execute("ALTER TABLE security_audit_events ADD COLUMN hash_version integer")
    _backfill_audit_chain()
    op.execute("ALTER TABLE security_audit_events ALTER COLUMN sequence SET NOT NULL")
    op.execute("ALTER TABLE security_audit_events ALTER COLUMN hash_version SET NOT NULL")
    op.execute("ALTER TABLE security_audit_events ALTER COLUMN hash_version SET DEFAULT 3")
    op.execute(
        "ALTER TABLE security_audit_events ADD CONSTRAINT ck_audit_sequence_positive "
        "CHECK (sequence > 0), ADD CONSTRAINT ck_audit_hash_version CHECK (hash_version IN (1,2,3))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_audit_organization_sequence "
        "ON security_audit_events(organization_id, sequence)"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_membership_or_revoked "
        "CHECK (membership_id IS NOT NULL OR revoked_at IS NOT NULL)"
    )
    # The definer has migration privilege; runtime cannot forge authority with app.* GUCs.
    op.execute("DROP TRIGGER trg_membership_role_tenant ON memberships")
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_membership_role_tenant() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE selected_role public.roles%ROWTYPE; old_code text;
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT code INTO old_code FROM public.roles WHERE id = OLD.role_id;
            IF old_code = 'platform_admin' THEN
              RAISE EXCEPTION 'platform_admin membership is immutable after bootstrap'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          SELECT * INTO selected_role FROM public.roles WHERE id = NEW.role_id;
          IF NOT FOUND OR (selected_role.organization_id IS NOT NULL AND
                          selected_role.organization_id <> NEW.organization_id) THEN
            RAISE EXCEPTION 'membership role does not belong to organization'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'INSERT' THEN
            PERFORM pg_advisory_xact_lock(hashtextextended('sentinelai-bootstrap', 2));
          END IF;
          IF selected_role.code = 'platform_admin' THEN
            IF TG_OP <> 'INSERT' OR EXISTS (SELECT 1 FROM public.memberships) OR
               NEW.status <> 'active' OR NEW.deleted_at IS NOT NULL OR
               selected_role.organization_id IS NOT NULL OR NOT selected_role.is_system THEN
              RAISE EXCEPTION 'platform_admin is reserved for the initial bootstrap membership'
                USING ERRCODE = '23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION enforce_membership_role_tenant() FROM PUBLIC;
        CREATE TRIGGER trg_membership_role_tenant BEFORE INSERT OR UPDATE OR DELETE ON memberships
          FOR EACH ROW EXECUTE FUNCTION enforce_membership_role_tenant();
        CREATE OR REPLACE FUNCTION enforce_session_membership() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
          IF NEW.organization_id::text IS DISTINCT FROM
             current_setting('app.organization_id', true) THEN
            RAISE EXCEPTION 'session organization context is inconsistent'
              USING ERRCODE = '23514';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text, 1));
          PERFORM 1 FROM public.memberships m
            JOIN public.users u ON u.id=m.user_id
            JOIN public.organizations o ON o.id=m.organization_id
            WHERE m.id=NEW.membership_id AND m.user_id=NEW.user_id
              AND m.organization_id=NEW.organization_id AND m.status='active'
              AND m.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
              AND o.status='active' AND o.deleted_at IS NULL
            FOR SHARE OF m, u, o;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'session membership or principal is inactive or inconsistent'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION enforce_session_membership() FROM PUBLIC;
        CREATE FUNCTION enforce_audit_sequence() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE previous_sequence bigint; previous_hash text;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text, 0));
          SELECT sequence, event_hash INTO previous_sequence, previous_hash
            FROM public.security_audit_events WHERE organization_id=NEW.organization_id
            ORDER BY sequence DESC LIMIT 1;
          IF NEW.hash_version <> 3 OR NEW.sequence <> COALESCE(previous_sequence, 0)+1
             OR NEW.prev_hash IS DISTINCT FROM previous_hash THEN
            RAISE EXCEPTION 'audit sequence, predecessor or hash version is invalid'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION enforce_audit_sequence() FROM PUBLIC;
        CREATE TRIGGER trg_audit_sequence BEFORE INSERT ON security_audit_events
          FOR EACH ROW EXECUTE FUNCTION enforce_audit_sequence();
    """)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='sentinelai_runtime') THEN
            REVOKE ALL ON users FROM sentinelai_runtime;
            GRANT SELECT, INSERT ON users TO sentinelai_runtime;
            REVOKE UPDATE ON outbox_events FROM sentinelai_runtime;
            REVOKE UPDATE ON password_credentials, sessions, account_recovery_tokens
              FROM sentinelai_runtime;
            GRANT UPDATE (password_hash, changed_at) ON password_credentials TO sentinelai_runtime;
            GRANT UPDATE (revoked_at, last_seen_at, idle_expires_at)
              ON sessions TO sentinelai_runtime;
            GRANT UPDATE (consumed_at) ON account_recovery_tokens TO sentinelai_runtime;
            REVOKE CREATE ON SCHEMA public FROM PUBLIC, sentinelai_runtime;
          END IF;
        END $$;
    """)


def downgrade() -> None:
    # Canonical defaults/nullability remain: reversing them would reintroduce schema drift.
    # Refuse to discard the sequencing evidence of a nonempty audit history.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM security_audit_events")):
        raise RuntimeError("Downgrade revision 04 requires an empty ephemeral audit database.")
    op.execute("DROP TRIGGER trg_audit_sequence ON security_audit_events")
    op.execute("DROP FUNCTION enforce_audit_sequence()")
    op.execute("DROP INDEX uq_audit_organization_sequence")
    op.execute("ALTER TABLE security_audit_events DROP CONSTRAINT ck_audit_sequence_positive")
    op.execute("ALTER TABLE security_audit_events DROP CONSTRAINT ck_audit_hash_version")
    op.execute("ALTER TABLE security_audit_events DROP COLUMN sequence, DROP COLUMN hash_version")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT ck_sessions_membership_or_revoked")
