"""Persist operator policy and admission history; never activate an implicit policy."""

import sqlalchemy as sa

from alembic import op

revision = "20261007_08"
down_revision = "20261006_07"
branch_labels = None
depends_on = None

TABLES = (
    "lab_policy_revisions",
    "lab_policy_current",
    "asset_admission_history",
    "asset_admission_current",
)


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sentinelai_policy_publisher') THEN
            CREATE ROLE sentinelai_policy_publisher NOLOGIN NOSUPERUSER NOCREATEDB
              NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
          IF EXISTS (SELECT FROM pg_roles WHERE rolname='sentinelai_policy_publisher'
              AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolcanlogin)) THEN
            RAISE EXCEPTION 'unsafe policy publisher role';
          END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO sentinelai_policy_publisher;
        CREATE TABLE policy_publisher_tenants (
          role_name name NOT NULL,
          organization_id uuid NOT NULL REFERENCES organizations(id),
          PRIMARY KEY(role_name,organization_id)
        );
        ALTER TABLE policy_publisher_tenants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE policy_publisher_tenants FORCE ROW LEVEL SECURITY;
        CREATE POLICY publisher_identity ON policy_publisher_tenants
          TO sentinelai_policy_publisher USING(role_name=current_user);
        REVOKE ALL ON policy_publisher_tenants FROM PUBLIC, sentinelai_runtime;
        GRANT SELECT ON policy_publisher_tenants TO sentinelai_policy_publisher;
        CREATE TABLE lab_policy_revisions (
          organization_id uuid NOT NULL REFERENCES organizations(id),
          id uuid NOT NULL,
          sequence bigint NOT NULL CHECK(sequence>0),
          snapshot text NOT NULL CHECK(octet_length(snapshot)<=350000),
          policy_hash varchar(64) NOT NULL
            CHECK(policy_hash=encode(digest(snapshot,'sha256'),'hex')),
          provenance varchar(160) NOT NULL
            CHECK(provenance ~ '^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,159}$'),
          publisher name NOT NULL DEFAULT current_user,
          published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(organization_id,id),
          UNIQUE(organization_id,sequence)
        );
        CREATE TABLE lab_policy_current (
          organization_id uuid PRIMARY KEY REFERENCES organizations(id),
          revision_id uuid NOT NULL,
          FOREIGN KEY(organization_id,revision_id)
            REFERENCES lab_policy_revisions(organization_id,id)
        );
        CREATE TABLE asset_admission_history (
          organization_id uuid NOT NULL,
          asset_id uuid NOT NULL,
          generation bigint NOT NULL CHECK(generation>=0),
          admitted boolean NOT NULL,
          revision_id uuid,
          reason varchar(16) NOT NULL CHECK(reason IN ('legacy','created','policy','archived')),
          occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(organization_id,asset_id,generation),
          FOREIGN KEY(organization_id,asset_id) REFERENCES assets(organization_id,id),
          FOREIGN KEY(organization_id,revision_id)
            REFERENCES lab_policy_revisions(organization_id,id),
          CHECK((generation=0 AND NOT admitted AND revision_id IS NULL AND reason='legacy')
            OR (generation>0 AND revision_id IS NOT NULL AND reason<>'legacy'))
        );
        CREATE TABLE asset_admission_current (
          organization_id uuid NOT NULL,
          asset_id uuid NOT NULL,
          generation bigint NOT NULL,
          PRIMARY KEY(organization_id,asset_id),
          FOREIGN KEY(organization_id,asset_id,generation)
            REFERENCES asset_admission_history(organization_id,asset_id,generation)
        );
        INSERT INTO asset_admission_history(organization_id,asset_id,generation,admitted,reason)
          SELECT organization_id,id,0,false,'legacy' FROM assets;
        INSERT INTO asset_admission_current SELECT organization_id,id,0 FROM assets;
        ALTER TABLE evidence_operations ADD COLUMN admission_generation bigint;
        ALTER TABLE evidence_operations ADD CONSTRAINT fk_evidence_admission
          FOREIGN KEY(organization_id,asset_id,admission_generation)
          REFERENCES asset_admission_history(organization_id,asset_id,generation);
        CREATE FUNCTION require_evidence_generation() RETURNS trigger LANGUAGE plpgsql
          SET search_path=pg_catalog,public AS $$
        BEGIN
          IF NEW.admission_generation IS NULL OR NEW.admission_generation<=0 THEN
            RAISE EXCEPTION 'new evidence requires an admission generation' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION require_evidence_generation() FROM PUBLIC;
        CREATE TRIGGER evidence_generation BEFORE INSERT ON evidence_operations
          FOR EACH ROW EXECUTE FUNCTION require_evidence_generation();

        CREATE FUNCTION immutable_lab_history() RETURNS trigger LANGUAGE plpgsql
          SET search_path=pg_catalog,public AS $$
        BEGIN
          RAISE EXCEPTION 'laboratory history is immutable' USING ERRCODE='23514';
        END $$;
        REVOKE ALL ON FUNCTION immutable_lab_history() FROM PUBLIC;
        CREATE TRIGGER immutable_policy BEFORE UPDATE OR DELETE ON lab_policy_revisions
          FOR EACH ROW EXECUTE FUNCTION immutable_lab_history();
        CREATE TRIGGER immutable_admission BEFORE UPDATE OR DELETE ON asset_admission_history
          FOR EACH ROW EXECUTE FUNCTION immutable_lab_history();

        CREATE FUNCTION lab_policy_permits(p jsonb, target text) RETURNS boolean
          LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
          SELECT (p->'allowed_targets') ? target AND NOT (p->'excluded_targets') ? target
            AND NOT target::inet <<= ANY(ARRAY['0.0.0.0/8','127.0.0.0/8','169.254.0.0/16',
              '224.0.0.0/4','240.0.0.0/4']::inet[])
        $$;
        REVOKE ALL ON FUNCTION lab_policy_permits(jsonb,text) FROM PUBLIC;

        CREATE FUNCTION publish_lab_policy() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public,pg_temp AS $$
        DECLARE p jsonb; item jsonb; target text; prior bigint; a record; permitted boolean;
        BEGIN
          -- Never trust a caller-set tenant GUC for operational assignment.
          IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname=session_user AND NOT rolsuper
              AND NOT rolbypassrls AND NOT rolcreatedb AND NOT rolcreaterole)
            OR NOT pg_has_role(session_user,'sentinelai_policy_publisher','member')
            OR pg_has_role(session_user,'sentinelai_runtime','member')
            OR pg_has_role(session_user,'sentinelai_evidence_maintenance','member')
            OR NOT EXISTS(SELECT FROM policy_publisher_tenants
              WHERE role_name=session_user AND organization_id=NEW.organization_id)
            OR NEW.publisher<>session_user THEN
            RAISE EXCEPTION 'restricted provisioned publisher required' USING ERRCODE='42501';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text,1));
          SELECT r.sequence INTO prior FROM lab_policy_current c JOIN lab_policy_revisions r
            ON (r.organization_id,r.id)=(c.organization_id,c.revision_id)
            WHERE c.organization_id=NEW.organization_id;
          IF NEW.sequence<>coalesce(prior,0)+1 THEN
            RAISE EXCEPTION 'policy revision conflict' USING ERRCODE='40001';
          END IF;
          p:=NEW.snapshot::jsonb;
          IF jsonb_typeof(p)<>'object' OR jsonb_object_length_checked(p)<>4 THEN
            RAISE EXCEPTION 'invalid policy shape' USING ERRCODE='23514';
          END IF;
          IF NOT p ?& ARRAY['version','allowed_targets','excluded_targets',
                           'max_active_assets_per_tenant']
            OR p->>'version'<>'1' OR jsonb_typeof(p->'version')<>'number'
            OR jsonb_typeof(p->'max_active_assets_per_tenant')<>'number'
            OR (p->>'max_active_assets_per_tenant') !~ '^[0-9]+$'
            OR (p->>'max_active_assets_per_tenant')::numeric NOT BETWEEN 1 AND 10000 THEN
            RAISE EXCEPTION 'invalid policy fields' USING ERRCODE='23514';
          END IF;
          FOREACH target IN ARRAY ARRAY['allowed_targets','excluded_targets'] LOOP
            IF jsonb_typeof(p->target)<>'array' OR jsonb_array_length(p->target)>10000 THEN
              RAISE EXCEPTION 'invalid targets' USING ERRCODE='23514';
            END IF;
            IF (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(p->target)) THEN
              RAISE EXCEPTION 'duplicate targets' USING ERRCODE='23514';
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(p->target) LOOP
              IF jsonb_typeof(item)<>'string' OR (item#>>'{}') !~ '^([0-9]{1,3}\\.){3}[0-9]{1,3}$'
                OR host((item#>>'{}')::inet)<>(item#>>'{}') THEN
                RAISE EXCEPTION 'exact IPv4 required' USING ERRCODE='23514';
              END IF;
            END LOOP;
          END LOOP;
          -- Same application canonical bytes, including list order; no duplicate keys,
          -- alternate whitespace, ambiguous numeric encoding or hidden extra fields.
          IF NEW.snapshot <> '{"version":'||'1,"allowed_targets":'
            ||replace((p->'allowed_targets')::text,' ','')||',"excluded_targets":'
            ||replace((p->'excluded_targets')::text,' ','')
            ||',"max_active_assets_per_tenant":'||(p->>'max_active_assets_per_tenant')||'}' THEN
            RAISE EXCEPTION 'canonical policy required' USING ERRCODE='23514';
          END IF;
          INSERT INTO lab_policy_current VALUES(NEW.organization_id,NEW.id)
            ON CONFLICT(organization_id) DO UPDATE SET revision_id=excluded.revision_id;
          FOR a IN SELECT s.id,s.canonical_target,s.deleted_at,h.admitted,h.generation
            FROM assets s JOIN asset_admission_current c
              ON (c.organization_id,c.asset_id)=(s.organization_id,s.id)
            JOIN asset_admission_history h
              ON (h.organization_id,h.asset_id,h.generation)=
                 (c.organization_id,c.asset_id,c.generation)
            WHERE s.organization_id=NEW.organization_id ORDER BY s.id LOOP
            permitted:=a.deleted_at IS NULL AND lab_policy_permits(p,a.canonical_target);
            IF permitted<>a.admitted THEN
              INSERT INTO asset_admission_history
                (organization_id,asset_id,generation,admitted,revision_id,reason)
                VALUES(NEW.organization_id,a.id,a.generation+1,permitted,NEW.id,'policy');
              UPDATE asset_admission_current SET generation=a.generation+1
                WHERE organization_id=NEW.organization_id AND asset_id=a.id;
            END IF;
          END LOOP;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION publish_lab_policy() FROM PUBLIC;
        CREATE TRIGGER publish_policy AFTER INSERT ON lab_policy_revisions
          FOR EACH ROW EXECUTE FUNCTION publish_lab_policy();

        CREATE FUNCTION jsonb_object_length_checked(p jsonb) RETURNS bigint LANGUAGE sql IMMUTABLE
          SET search_path=pg_catalog AS $$ SELECT count(*) FROM jsonb_object_keys(p) $$;
        REVOKE ALL ON FUNCTION jsonb_object_length_checked(jsonb) FROM PUBLIC;

        CREATE FUNCTION bind_asset_admission() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public,pg_temp AS $$
        DECLARE r record; g bigint; admitted_now boolean;
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text,1));
          SELECT p.* INTO r FROM lab_policy_current c JOIN lab_policy_revisions p
            ON (p.organization_id,p.id)=(c.organization_id,c.revision_id)
            WHERE c.organization_id=NEW.organization_id;
          IF TG_OP='INSERT' THEN
            IF r.id IS NULL OR NOT lab_policy_permits(r.snapshot::jsonb,NEW.canonical_target)
              OR NEW.policy_hash<>r.policy_hash OR NEW.deleted_at IS NOT NULL THEN
              RAISE EXCEPTION 'published policy required for admission' USING ERRCODE='23514';
            END IF;
            INSERT INTO asset_admission_history
              (organization_id,asset_id,generation,admitted,revision_id,reason)
              VALUES(NEW.organization_id,NEW.id,1,true,r.id,'created');
            INSERT INTO asset_admission_current VALUES(NEW.organization_id,NEW.id,1);
          ELSIF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN
            SELECT h.generation,h.admitted INTO g,admitted_now FROM asset_admission_current c
              JOIN asset_admission_history h ON
              (h.organization_id,h.asset_id,h.generation)=(c.organization_id,c.asset_id,c.generation)
              WHERE c.organization_id=NEW.organization_id AND c.asset_id=NEW.id;
            IF admitted_now THEN
              INSERT INTO asset_admission_history
                (organization_id,asset_id,generation,admitted,revision_id,reason)
                VALUES(NEW.organization_id,NEW.id,g+1,false,r.id,'archived');
              UPDATE asset_admission_current SET generation=g+1
                WHERE organization_id=NEW.organization_id AND asset_id=NEW.id;
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION bind_asset_admission() FROM PUBLIC;
        CREATE TRIGGER bind_admission AFTER INSERT OR UPDATE ON assets
          FOR EACH ROW EXECUTE FUNCTION bind_asset_admission();

        CREATE UNIQUE INDEX uq_policy_audit_revision ON security_audit_events
          (organization_id,resource_id) WHERE action='lab_policy.published';
        CREATE UNIQUE INDEX uq_policy_outbox_revision ON outbox_events
          (organization_id,aggregate_id) WHERE event_type='lab_policy.published';

        -- Validate direct SQL as well as the CLI. No locks are acquired here:
        -- revision insertion already holds the organization lock, before audit's lock.
        CREATE FUNCTION validate_policy_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public,pg_temp AS $$
        DECLARE r record; revision_id uuid; content jsonb; expected jsonb; valid boolean;
        BEGIN
          IF TG_TABLE_NAME='security_audit_events' THEN
            revision_id:=NEW.resource_id;
            content:=NEW.details::jsonb;
            valid:=NEW.action='lab_policy.published' AND NEW.resource_type='lab_policy'
              AND NEW.actor_type='system' AND NEW.actor_user_id IS NULL
              AND NEW.outcome='success' AND NEW.request_id=revision_id::text;
          ELSE
            revision_id:=NEW.aggregate_id;
            content:=NEW.payload::jsonb;
            valid:=NEW.event_type='lab_policy.published' AND NEW.aggregate_type='lab_policy'
              AND NEW.idempotency_key='lab_policy:'||revision_id::text
              AND NEW.published_at IS NULL AND NEW.attempts=0;
          END IF;
          SELECT * INTO r FROM lab_policy_revisions
            WHERE organization_id=NEW.organization_id AND id=revision_id;
          IF r.id IS NULL OR r.publisher<>session_user
            OR NOT pg_has_role(session_user,'sentinelai_policy_publisher','member') THEN
            RAISE EXCEPTION 'policy event requires its authenticated publisher and revision'
              USING ERRCODE='23514', CONSTRAINT='policy_event_authority';
          END IF;
          expected:=jsonb_build_object('sequence',r.sequence,'policy_hash',r.policy_hash,
            'publisher',r.publisher,'provenance',r.provenance);
          IF valid IS DISTINCT FROM true OR content IS DISTINCT FROM expected THEN
            RAISE EXCEPTION 'policy event contradicts authoritative revision'
              USING ERRCODE='23514', CONSTRAINT='policy_event_content';
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION validate_policy_event() FROM PUBLIC;
        CREATE TRIGGER validate_policy_audit BEFORE INSERT ON security_audit_events
          FOR EACH ROW WHEN (NEW.action='lab_policy.published' OR NEW.resource_type='lab_policy')
          EXECUTE FUNCTION validate_policy_event();
        CREATE TRIGGER validate_policy_outbox BEFORE INSERT ON outbox_events
          FOR EACH ROW WHEN
            (NEW.event_type='lab_policy.published' OR NEW.aggregate_type='lab_policy')
          EXECUTE FUNCTION validate_policy_event();

        CREATE FUNCTION require_policy_events() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
          SET search_path=pg_catalog,public,pg_temp AS $$
        BEGIN
          IF NOT EXISTS(SELECT FROM security_audit_events WHERE organization_id=NEW.organization_id
              AND resource_id=NEW.id AND action='lab_policy.published' AND outcome='success')
            OR NOT EXISTS(SELECT FROM outbox_events WHERE organization_id=NEW.organization_id
              AND aggregate_id=NEW.id AND event_type='lab_policy.published') THEN
            RAISE EXCEPTION 'transactional policy events required'
              USING ERRCODE='23514', CONSTRAINT='policy_events_required';
          END IF;
          RETURN NULL;
        END $$;
        REVOKE ALL ON FUNCTION require_policy_events() FROM PUBLIC;
        CREATE CONSTRAINT TRIGGER policy_events AFTER INSERT ON lab_policy_revisions
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_policy_events();
    """)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, sentinelai_runtime")
        op.execute(f"GRANT SELECT ON {table} TO sentinelai_runtime,sentinelai_policy_publisher")
        scope = "organization_id::text=current_setting('app.organization_id',true)"
        op.execute(f"CREATE POLICY {table}_runtime ON {table} TO sentinelai_runtime USING({scope})")
        op.execute(
            f"CREATE POLICY {table}_publisher ON {table} TO sentinelai_policy_publisher "
            f"USING({scope} AND EXISTS(SELECT FROM policy_publisher_tenants t "
            f"WHERE t.role_name=current_user AND t.organization_id={table}.organization_id))"
        )
    op.execute(
        "GRANT INSERT (organization_id,id,sequence,snapshot,policy_hash,provenance) "
        "ON lab_policy_revisions TO sentinelai_policy_publisher"
    )
    for table in ("security_audit_events", "outbox_events"):
        scope = (
            "organization_id::text=current_setting('app.organization_id',true) "
            "AND EXISTS(SELECT FROM policy_publisher_tenants t WHERE t.role_name=current_user "
            f"AND t.organization_id={table}.organization_id)"
        )
        # Existing PUBLIC policies must not defeat operational assignment checks.
        op.execute(
            f"CREATE POLICY {table}_publisher_scope ON {table} AS RESTRICTIVE "
            f"TO sentinelai_policy_publisher USING({scope}) WITH CHECK({scope})"
        )
        column = "action" if table == "security_audit_events" else "event_type"
        identity = (
            " AND actor_type='system' AND actor_user_id IS NULL AND resource_type='lab_policy'"
            if table == "security_audit_events"
            else " AND aggregate_type='lab_policy'"
        )
        op.execute(
            f"CREATE POLICY {table}_publisher_append ON {table} AS RESTRICTIVE FOR INSERT "
            f"TO sentinelai_policy_publisher WITH CHECK({column}='lab_policy.published'{identity})"
        )
        op.execute(f"GRANT SELECT ON {table} TO sentinelai_policy_publisher")
        columns = (
            "id,organization_id,sequence,hash_version,occurred_at,actor_user_id,actor_type,"
            "action,resource_type,resource_id,outcome,request_id,details,prev_hash,event_hash"
            if table == "security_audit_events"
            else "id,organization_id,aggregate_type,aggregate_id,event_type,idempotency_key,"
            "payload,created_at"
        )
        op.execute(f"GRANT INSERT ({columns}) ON {table} TO sentinelai_policy_publisher")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT FROM lab_policy_revisions)")):
        raise RuntimeError("Downgrade 08 refuses published policy history; preserve the database.")
    op.execute("""
        DROP TRIGGER bind_admission ON assets;
        DROP TRIGGER validate_policy_audit ON security_audit_events;
        DROP TRIGGER validate_policy_outbox ON outbox_events;
        DROP INDEX uq_policy_audit_revision,uq_policy_outbox_revision;
        DROP FUNCTION validate_policy_event();
        DROP TRIGGER evidence_generation ON evidence_operations;
        DROP FUNCTION require_evidence_generation();
        ALTER TABLE evidence_operations DROP CONSTRAINT fk_evidence_admission;
        ALTER TABLE evidence_operations DROP COLUMN admission_generation;
        DROP TABLE asset_admission_current,asset_admission_history,
          lab_policy_current,lab_policy_revisions;
        DROP POLICY security_audit_events_publisher_scope ON security_audit_events;
        DROP POLICY security_audit_events_publisher_append ON security_audit_events;
        DROP POLICY outbox_events_publisher_scope ON outbox_events;
        DROP POLICY outbox_events_publisher_append ON outbox_events;
        REVOKE ALL ON security_audit_events,outbox_events FROM sentinelai_policy_publisher;
        REVOKE INSERT (id,organization_id,sequence,hash_version,occurred_at,actor_user_id,
          actor_type,action,resource_type,resource_id,outcome,request_id,details,prev_hash,event_hash)
          ON security_audit_events FROM sentinelai_policy_publisher;
        REVOKE INSERT (id,organization_id,aggregate_type,aggregate_id,event_type,idempotency_key,
          payload,created_at) ON outbox_events FROM sentinelai_policy_publisher;
        DROP TABLE policy_publisher_tenants;
        DROP FUNCTION bind_asset_admission(),publish_lab_policy(),require_policy_events(),
          immutable_lab_history(),lab_policy_permits(jsonb,text),jsonb_object_length_checked(jsonb);
    """)
    # The cluster-scoped NOLOGIN role may serve other databases; do not drop it.
