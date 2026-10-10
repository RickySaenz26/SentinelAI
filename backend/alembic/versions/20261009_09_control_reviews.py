"""Human technical-control history; never ownership or scan authorization."""

import sqlalchemy as sa

from alembic import op

revision = "20261009_09"
down_revision = "20261007_08"
branch_labels = None
depends_on = None

PERMISSIONS = {
    "submit": ("org_owner", "security_manager", "analyst"),
    "read": ("org_owner", "security_manager", "auditor"),
    "read_own": ("analyst",),
    "decide": ("org_owner", "security_manager"),
    "withdraw": ("org_owner", "security_manager", "analyst"),
    "revoke": ("org_owner", "security_manager"),
    "summary": ("org_owner", "security_manager", "analyst", "auditor", "viewer", "platform_admin"),
}


def upgrade() -> None:
    for action, roles in PERMISSIONS.items():
        op.get_bind().execute(
            sa.text(
                "INSERT INTO permissions(id,code,resource,action) "
                "VALUES(gen_random_uuid(),:code,'control',:action)"
            ),
            {"code": f"control:{action}", "action": action},
        )
        for role in roles:
            op.get_bind().execute(
                sa.text(
                    "INSERT INTO role_permissions SELECT r.id,p.id "
                    "FROM roles r CROSS JOIN permissions p "
                    "WHERE r.is_system AND r.organization_id IS NULL "
                    "AND r.code=:role AND p.code=:code"
                ),
                {"role": role, "code": f"control:{action}"},
            )
    op.execute("""
      ALTER TABLE security_audit_events ADD CONSTRAINT uq_control_audit_identity
        UNIQUE(organization_id,id);
      ALTER TABLE evidence_versions ADD CONSTRAINT uq_control_evidence_binding
        UNIQUE(organization_id,asset_id,version,operation_id);
      CREATE TABLE control_review_requests (
        organization_id uuid NOT NULL, id uuid NOT NULL,
        asset_id uuid NOT NULL, evidence_id uuid NOT NULL, evidence_version integer NOT NULL,
        presenter_id uuid NOT NULL, author_id uuid NOT NULL,
        asset_version integer NOT NULL CHECK(asset_version>0), snapshot jsonb NOT NULL,
        generation bigint NOT NULL CHECK(generation>0), renews_review_id uuid,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        retention_until timestamptz NOT NULL,
        PRIMARY KEY(organization_id,id), UNIQUE(organization_id,id,asset_id),
        FOREIGN KEY(organization_id,asset_id) REFERENCES assets(organization_id,id),
        FOREIGN KEY(organization_id,asset_id,evidence_version,evidence_id)
          REFERENCES evidence_versions(organization_id,asset_id,version,operation_id),
        FOREIGN KEY(organization_id,asset_id,generation)
          REFERENCES asset_admission_history(organization_id,asset_id,generation),
        FOREIGN KEY(organization_id,renews_review_id,asset_id)
          REFERENCES control_review_requests(organization_id,id,asset_id),
        CHECK(presenter_id=author_id), CHECK(retention_until>created_at)
      );
      CREATE TABLE control_review_projections (
        organization_id uuid NOT NULL, review_id uuid NOT NULL, asset_id uuid NOT NULL,
        presenter_id uuid NOT NULL, version integer NOT NULL DEFAULT 1,
        state text NOT NULL DEFAULT 'pending'
          CHECK(state IN ('pending','approved','rejected','withdrawn','invalidated')),
        decided_at timestamptz, valid_until timestamptz,
        revoked_at timestamptz, invalidated_at timestamptz, superseded_by uuid,
        PRIMARY KEY(organization_id,review_id),
        FOREIGN KEY(organization_id,review_id,asset_id)
          REFERENCES control_review_requests(organization_id,id,asset_id),
        FOREIGN KEY(organization_id,superseded_by,asset_id)
          REFERENCES control_review_requests(organization_id,id,asset_id),
        CHECK(version>0), CHECK(valid_until IS NULL OR state='approved')
      );
      CREATE UNIQUE INDEX uq_control_pending ON control_review_projections
        (organization_id,asset_id) WHERE state='pending';
      CREATE TABLE control_review_events (
        organization_id uuid NOT NULL, id uuid NOT NULL, review_id uuid NOT NULL,
        kind text NOT NULL CHECK(kind IN ('submitted','approved','rejected','withdrawn',
          'invalidated','revoked','superseded')),
        actor_id uuid, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expected_version integer, asset_version integer NOT NULL,
        reason_code text NOT NULL, checklist_version integer, checklist jsonb,
        read_audit_id uuid, replacement_id uuid,
        PRIMARY KEY(organization_id,id),
        FOREIGN KEY(organization_id,review_id)
          REFERENCES control_review_requests(organization_id,id),
        FOREIGN KEY(organization_id,read_audit_id)
          REFERENCES security_audit_events(organization_id,id),
        FOREIGN KEY(organization_id,replacement_id)
          REFERENCES control_review_requests(organization_id,id)
      );
      CREATE UNIQUE INDEX uq_control_event_kind ON control_review_events
        (organization_id,review_id,kind);
      CREATE UNIQUE INDEX uq_control_decision ON control_review_events
        (organization_id,review_id) WHERE kind IN ('approved','rejected','withdrawn');

      CREATE FUNCTION control_actor() RETURNS TABLE(org uuid,usr uuid,role_code text,sid uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
        SELECT s.organization_id,s.user_id,r.code::text,s.id FROM sessions s
          JOIN memberships m ON (m.organization_id,m.id,m.user_id)=
            (s.organization_id,s.membership_id,s.user_id)
          JOIN users u ON u.id=s.user_id JOIN organizations o ON o.id=s.organization_id
          JOIN roles r ON r.id=m.role_id
        WHERE s.id=nullif(current_setting('app.control_session',true),'')::uuid
          AND s.organization_id::text=current_setting('app.organization_id',true)
          AND s.user_id::text=current_setting('app.control_user',true)
          AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
          AND s.idle_expires_at>clock_timestamp() AND m.status='active'
          AND m.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
          AND o.status='active' AND o.deleted_at IS NULL
      $$;
      CREATE FUNCTION control_permitted(action text) RETURNS boolean LANGUAGE sql STABLE
        SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
        SELECT EXISTS(SELECT FROM control_actor() a JOIN sessions s ON s.id=a.sid
          JOIN memberships m ON m.id=s.membership_id
          JOIN role_permissions rp ON rp.role_id=m.role_id
          JOIN permissions p ON p.id=rp.permission_id
          WHERE p.code='control:'||$1 AND
            ($1<>'decide' OR EXISTS(SELECT FROM role_permissions erp JOIN permissions ep
              ON ep.id=erp.permission_id WHERE erp.role_id=m.role_id AND ep.code='evidence:read'))
            AND
            ($1='summary' OR a.role_code IN ('org_owner','security_manager','analyst','auditor')))
      $$;
      CREATE FUNCTION control_visible(presenter uuid) RETURNS boolean LANGUAGE sql STABLE
        SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
        SELECT control_permitted('read') OR (control_permitted('read_own')
          AND presenter=(SELECT usr FROM control_actor()))
      $$;
      CREATE FUNCTION control_compatible(s assets,r control_review_requests) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
        SELECT s.deleted_at IS NULL AND s.version>=r.asset_version
          AND (s.organization_id,s.id,s.type,s.canonical_target,s.ownership_status)=
            (r.organization_id,r.asset_id,r.snapshot->>'type',r.snapshot->>'canonical_target',
             r.snapshot->>'ownership_status') AND s.ownership_status='unverified'
      $$;
      CREATE FUNCTION control_admitted(r control_review_requests) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
        SELECT EXISTS(SELECT FROM asset_admission_current c JOIN asset_admission_history h
          ON (h.organization_id,h.asset_id,h.generation)=(c.organization_id,c.asset_id,c.generation)
          WHERE c.organization_id=r.organization_id AND c.asset_id=r.asset_id
            AND c.generation=r.generation AND h.admitted)
      $$;

      -- Canonical ASCII-only control event material, matching audit hash version 3.
      CREATE FUNCTION control_json(p jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE
        SET search_path=pg_catalog,public AS $$
      BEGIN
        IF jsonb_typeof(p)='object' THEN
          RETURN '{'||coalesce((SELECT string_agg(to_jsonb(key)::text||':'||control_json(value),
            ',' ORDER BY key COLLATE "C") FROM jsonb_each(p)),'')||'}';
        ELSIF jsonb_typeof(p)='array' THEN
          RETURN '['||coalesce((SELECT string_agg(control_json(value),',' ORDER BY ordinal)
            FROM jsonb_array_elements(p) WITH ORDINALITY AS x(value,ordinal)),'')||']';
        END IF;
        RETURN p::text;
      END $$;
      CREATE FUNCTION control_log(e control_review_events) RETURNS void LANGUAGE plpgsql
        SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      DECLARE previous record; n bigint; aid uuid:=gen_random_uuid(); details jsonb;
        material jsonb; moment text; action text:='control.'||e.kind;
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(e.organization_id::text,0));
        SELECT sequence,event_hash INTO previous FROM security_audit_events
          WHERE organization_id=e.organization_id ORDER BY sequence DESC LIMIT 1;
        n:=coalesce(previous.sequence,0)+1;
        details:=jsonb_build_object('review_id',e.review_id,'event_id',e.id,
          'reason_code',e.reason_code,'asset_version',e.asset_version);
        moment:=to_char(e.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||
          CASE WHEN to_char(e.occurred_at,'US')='000000' THEN ''
            ELSE '.'||to_char(e.occurred_at,'US') END||'Z';
        material:=jsonb_build_object('action',action,'actor_type',
          CASE WHEN e.actor_id IS NULL THEN 'system' ELSE 'user' END,
          'actor_user_id',e.actor_id,'details',details,'event_id',aid,'occurred_at',moment,
          'organization_id',e.organization_id,'outcome','success',
          'previous_hash',previous.event_hash,'request_id',e.id::text,
          'resource_id',e.review_id,'resource_type','control_review',
          'sequence',n,'hash_version',3);
        INSERT INTO security_audit_events(id,organization_id,sequence,hash_version,occurred_at,
          actor_user_id,actor_type,action,resource_type,resource_id,outcome,request_id,
          details,prev_hash,event_hash)
          VALUES(aid,e.organization_id,n,3,e.occurred_at,e.actor_id,
            CASE WHEN e.actor_id IS NULL THEN 'system' ELSE 'user' END,
            action,'control_review',e.review_id,'success',e.id::text,details::json,
            previous.event_hash,encode(digest(control_json(material),'sha256'),'hex'));
        INSERT INTO outbox_events(id,organization_id,aggregate_type,aggregate_id,event_type,
          idempotency_key,payload,created_at)
          VALUES(gen_random_uuid(),e.organization_id,'control_review',e.review_id,action,
            'control:'||e.id::text,details::json,e.occurred_at);
      END $$;

      CREATE FUNCTION guard_control_request() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public,pg_temp AS $$
      DECLARE a record; s assets; ev record; parent control_review_projections; expired record;
      BEGIN
        SELECT * INTO a FROM control_actor();
        IF a.usr IS NULL OR NEW.organization_id<>a.org OR NOT control_permitted('submit')
          OR a.role_code NOT IN ('org_owner','security_manager','analyst') THEN
          RAISE EXCEPTION 'CONTROL_FORBIDDEN' USING ERRCODE='42501';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(a.org::text,1));
        SELECT * INTO a FROM control_actor();
        IF a.usr IS NULL OR NEW.organization_id<>a.org OR NOT control_permitted('submit') THEN
          RAISE EXCEPTION 'CONTROL_FORBIDDEN' USING ERRCODE='42501';
        END IF;
        SELECT * INTO s FROM assets WHERE organization_id=a.org AND id=NEW.asset_id FOR UPDATE;
        IF s.id IS NULL OR s.deleted_at IS NOT NULL OR s.version<>NEW.asset_version THEN
          RAISE EXCEPTION 'CONTROL_VERSION_CONFLICT' USING ERRCODE='23514',
            CONSTRAINT='control_asset_version';
        END IF;
        SELECT o.*,v.created_at AS submitted_at INTO ev FROM evidence_versions v
          JOIN evidence_operations o ON (o.organization_id,o.id,o.asset_id,o.version)=
            (v.organization_id,v.operation_id,v.asset_id,v.version)
          WHERE v.organization_id=a.org AND v.asset_id=s.id
            AND v.operation_id=NEW.evidence_id AND v.version=NEW.evidence_version;
        IF ev.id IS NULL OR ev.state<>'committed' OR ev.actor_id<>a.usr
          OR ev.asset_version>s.version THEN
          RAISE EXCEPTION 'CONTROL_EVIDENCE_REFERENCE' USING ERRCODE='23514',
            CONSTRAINT='control_evidence_owner';
        END IF;
        NEW.presenter_id:=a.usr; NEW.author_id:=ev.actor_id;
        NEW.created_at:=clock_timestamp(); NEW.retention_until:=ev.submitted_at+interval '90 days';
        NEW.snapshot:=jsonb_build_object('type',s.type,'canonical_target',s.canonical_target,
          'ownership_status',s.ownership_status,'display_name',s.display_name,
          'criticality',s.criticality,'version',s.version,'asset_id',s.id,'organization_id',a.org);
        SELECT c.generation INTO NEW.generation FROM asset_admission_current c
          WHERE c.organization_id=a.org AND c.asset_id=s.id;
        IF NEW.generation IS NULL OR NOT control_admitted(NEW)
          OR NEW.retention_until<=NEW.created_at THEN
          RAISE EXCEPTION 'CONTROL_NOT_ADMITTED_OR_RETAINED' USING ERRCODE='23514',
            CONSTRAINT='control_admission_retention';
        END IF;
        IF NEW.renews_review_id IS NULL AND EXISTS(SELECT FROM control_review_projections
          WHERE organization_id=a.org AND asset_id=s.id AND state='approved') THEN
          RAISE EXCEPTION 'CONTROL_RENEWAL_LINK_REQUIRED' USING ERRCODE='23514',
            CONSTRAINT='control_renewal';
        END IF;
        IF NEW.renews_review_id IS NOT NULL THEN
          SELECT * INTO parent FROM control_review_projections
            WHERE organization_id=a.org AND review_id=NEW.renews_review_id;
          IF parent.review_id IS NULL OR parent.asset_id<>s.id OR parent.state<>'approved'
            OR NOT control_visible(parent.presenter_id) THEN
            RAISE EXCEPTION 'CONTROL_RENEWAL_REFERENCE' USING ERRCODE='23514',
              CONSTRAINT='control_renewal';
          END IF;
        END IF;
        FOR expired IN SELECT p.review_id FROM control_review_projections p
          JOIN control_review_requests q ON (q.organization_id,q.id)=
            (p.organization_id,p.review_id)
          WHERE p.organization_id=a.org AND p.asset_id=s.id AND p.state='pending'
            AND q.retention_until<=clock_timestamp() ORDER BY p.review_id FOR UPDATE OF p LOOP
          INSERT INTO control_review_events(organization_id,id,review_id,kind,reason_code)
            VALUES(a.org,gen_random_uuid(),expired.review_id,'invalidated','retention_expired');
        END LOOP;
        RETURN NEW;
      END $$;
      CREATE TRIGGER control_request_guard BEFORE INSERT ON control_review_requests
        FOR EACH ROW EXECUTE FUNCTION guard_control_request();
      CREATE TRIGGER control_request_immutable BEFORE UPDATE OR DELETE ON control_review_requests
        FOR EACH ROW EXECUTE FUNCTION immutable_lab_history();

      CREATE FUNCTION guard_control_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public,pg_temp AS $$
      DECLARE a record; r control_review_requests; p control_review_projections; s assets;
      BEGIN
        SELECT * INTO r FROM control_review_requests
          WHERE organization_id=NEW.organization_id AND id=NEW.review_id;
        IF r.id IS NULL THEN
          RAISE EXCEPTION 'CONTROL_NOT_FOUND' USING ERRCODE='23514',CONSTRAINT='control_reference';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(r.organization_id::text,1));
        SELECT * INTO s FROM assets WHERE organization_id=r.organization_id
          AND id=r.asset_id FOR UPDATE;
        SELECT * INTO p FROM control_review_projections WHERE organization_id=r.organization_id
          AND review_id=r.id FOR UPDATE;
        NEW.occurred_at:=clock_timestamp(); NEW.asset_version:=s.version;
        IF NEW.kind IN ('submitted','invalidated','superseded') THEN
          IF pg_trigger_depth()<2 THEN
            RAISE EXCEPTION 'CONTROL_INTERNAL_EVENT' USING ERRCODE='42501';
          END IF;
          RETURN NEW;
        END IF;
        SELECT * INTO a FROM control_actor();
        IF a.usr IS NULL OR a.org<>r.organization_id OR a.role_code='platform_admin' THEN
          RAISE EXCEPTION 'CONTROL_FORBIDDEN' USING ERRCODE='42501';
        END IF;
        NEW.actor_id:=a.usr;
        IF NEW.kind IN ('withdrawn','revoked') AND
          (NEW.checklist IS NOT NULL OR NEW.checklist_version IS NOT NULL) THEN
          RAISE EXCEPTION 'CONTROL_CHECKLIST_NOT_APPLICABLE' USING ERRCODE='23514',
            CONSTRAINT='control_checklist';
        END IF;
        IF NEW.expected_version IS DISTINCT FROM p.version THEN
          RAISE EXCEPTION 'CONTROL_VERSION_CONFLICT' USING ERRCODE='23514',
            CONSTRAINT='control_review_version';
        END IF;
        IF NEW.kind='revoked' THEN
          IF NOT control_permitted('revoke') OR a.role_code NOT IN ('org_owner','security_manager')
            OR p.state<>'approved' OR p.revoked_at IS NOT NULL
            OR NEW.reason_code NOT IN ('confidence_withdrawn','error_found') THEN
            RAISE EXCEPTION 'CONTROL_REVOCATION_DENIED' USING ERRCODE='23514',
              CONSTRAINT='control_revocation';
          END IF;
        ELSE
          IF p.state<>'pending' OR NOT control_compatible(s,r) OR NOT control_admitted(r)
            OR r.retention_until<=NEW.occurred_at THEN
            RAISE EXCEPTION 'CONTROL_NOT_PENDING' USING ERRCODE='23514',
              CONSTRAINT='control_pending_current';
          END IF;
          IF NEW.kind='withdrawn' THEN
            IF NOT control_permitted('withdraw') OR a.usr<>r.presenter_id
              OR NEW.reason_code<>'presenter_withdrawal' THEN
              RAISE EXCEPTION 'CONTROL_WITHDRAWAL_DENIED' USING ERRCODE='23514',
                CONSTRAINT='control_withdrawal';
            END IF;
          ELSE
            IF NOT control_permitted('decide')
              OR a.role_code NOT IN ('org_owner','security_manager')
              OR a.usr IN (r.presenter_id,r.author_id) THEN
              RAISE EXCEPTION 'CONTROL_SEPARATION_REQUIRED' USING ERRCODE='23514',
                CONSTRAINT='control_separation';
            END IF;
            SELECT id INTO NEW.read_audit_id FROM security_audit_events
              WHERE organization_id=r.organization_id AND actor_user_id=a.usr
                AND action='evidence.content_read' AND resource_type='evidence'
                AND resource_id=r.evidence_id AND outcome='success'
                AND details::jsonb=jsonb_build_object('version',r.evidence_version)
                AND occurred_at>r.created_at ORDER BY sequence DESC LIMIT 1;
            IF NEW.read_audit_id IS NULL THEN
              RAISE EXCEPTION 'CONTROL_READING_REQUIRED' USING ERRCODE='23514',
                CONSTRAINT='control_reading';
            END IF;
            IF NEW.checklist_version IS DISTINCT FROM 1 OR NEW.checklist IS NULL
              OR jsonb_typeof(NEW.checklist)<>'object'
              OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(NEW.checklist) key)
                IS DISTINCT FROM
                  ARRAY['administrative_control','console_identity','inventory_match']
              OR EXISTS(SELECT FROM jsonb_each_text(NEW.checklist)
                WHERE value IS NULL OR value NOT IN
                  ('confirmed','not_confirmed','not_assessable')) THEN
              RAISE EXCEPTION 'CONTROL_CHECKLIST_INVALID' USING ERRCODE='23514',
                CONSTRAINT='control_checklist';
            END IF;
            IF (NEW.kind='approved' AND (NEW.reason_code<>'control_confirmed'
                OR EXISTS(SELECT FROM jsonb_each_text(NEW.checklist) WHERE value<>'confirmed')))
              OR (NEW.kind='rejected' AND NEW.reason_code NOT IN
                ('mismatch','incomplete','not_assessable')) THEN
              RAISE EXCEPTION 'CONTROL_JUDGEMENT_INVALID' USING ERRCODE='23514',
                CONSTRAINT='control_judgement';
            END IF;
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER control_event_guard BEFORE INSERT ON control_review_events
        FOR EACH ROW EXECUTE FUNCTION guard_control_event();
      CREATE TRIGGER control_event_immutable BEFORE UPDATE OR DELETE ON control_review_events
        FOR EACH ROW EXECUTE FUNCTION immutable_lab_history();

      CREATE FUNCTION apply_control_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public,pg_temp AS $$
      DECLARE r control_review_requests; old record;
      BEGIN
        SELECT * INTO r FROM control_review_requests
          WHERE organization_id=NEW.organization_id AND id=NEW.review_id;
        IF NEW.kind='submitted' THEN
          INSERT INTO control_review_projections(organization_id,review_id,asset_id,presenter_id)
            VALUES(r.organization_id,r.id,r.asset_id,r.presenter_id);
        ELSIF NEW.kind IN ('approved','rejected','withdrawn') THEN
          UPDATE control_review_projections SET state=NEW.kind,decided_at=NEW.occurred_at,
            version=version+1,valid_until=CASE WHEN NEW.kind='approved'
              THEN least(NEW.occurred_at+interval '30 days',r.retention_until) ELSE NULL END
            WHERE organization_id=r.organization_id AND review_id=r.id;
          IF NEW.kind='approved' THEN
            FOR old IN SELECT review_id FROM control_review_projections
              WHERE organization_id=r.organization_id AND asset_id=r.asset_id
                AND review_id<>r.id AND state='approved' AND superseded_by IS NULL
              ORDER BY review_id FOR UPDATE LOOP
              INSERT INTO control_review_events(organization_id,id,review_id,kind,actor_id,
                reason_code,replacement_id) VALUES(r.organization_id,gen_random_uuid(),
                  old.review_id,'superseded',NEW.actor_id,'new_approval',r.id);
            END LOOP;
          END IF;
        ELSIF NEW.kind='revoked' THEN
          UPDATE control_review_projections SET revoked_at=NEW.occurred_at,version=version+1
            WHERE organization_id=r.organization_id AND review_id=r.id;
        ELSIF NEW.kind='superseded' THEN
          UPDATE control_review_projections SET superseded_by=NEW.replacement_id,version=version+1
            WHERE organization_id=r.organization_id AND review_id=r.id;
        ELSE
          UPDATE control_review_projections SET invalidated_at=NEW.occurred_at,version=version+1,
            state=CASE WHEN state='pending' THEN 'invalidated' ELSE state END
            WHERE organization_id=r.organization_id AND review_id=r.id;
        END IF;
        PERFORM control_log(NEW);
        RETURN NULL;
      END $$;
      CREATE TRIGGER control_event_apply AFTER INSERT ON control_review_events
        FOR EACH ROW EXECUTE FUNCTION apply_control_event();
      CREATE FUNCTION begin_control_review() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public,pg_temp AS $$
      BEGIN
        INSERT INTO control_review_events(organization_id,id,review_id,kind,actor_id,reason_code)
          VALUES(NEW.organization_id,gen_random_uuid(),NEW.id,'submitted',
            NEW.presenter_id,'evidence_presented');
        RETURN NULL;
      END $$;
      CREATE TRIGGER control_request_begin AFTER INSERT ON control_review_requests
        FOR EACH ROW EXECUTE FUNCTION begin_control_review();

      CREATE FUNCTION invalidate_control_reviews() RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      DECLARE target uuid; r control_review_requests; s assets; reason text;
      BEGIN
        IF TG_TABLE_NAME='assets' THEN target:=NEW.id; ELSE target:=NEW.asset_id; END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.organization_id::text,1));
        SELECT * INTO s FROM assets WHERE organization_id=NEW.organization_id
          AND id=target FOR UPDATE;
        FOR r IN SELECT q.* FROM control_review_requests q JOIN control_review_projections p
          ON (p.organization_id,p.review_id)=(q.organization_id,q.id)
          WHERE q.organization_id=NEW.organization_id AND q.asset_id=target
            AND p.state IN ('pending','approved') AND p.invalidated_at IS NULL
            AND p.revoked_at IS NULL AND p.superseded_by IS NULL ORDER BY q.id LOOP
          reason:=CASE WHEN NOT control_compatible(s,r) THEN 'asset_incompatible'
            WHEN NOT control_admitted(r) THEN 'admission_changed' ELSE NULL END;
          IF reason IS NOT NULL THEN
            INSERT INTO control_review_events(organization_id,id,review_id,kind,reason_code)
              VALUES(r.organization_id,gen_random_uuid(),r.id,'invalidated',reason);
          END IF;
        END LOOP;
        RETURN NULL;
      END $$;
      CREATE TRIGGER control_admission_invalidated AFTER UPDATE ON asset_admission_current
        FOR EACH ROW EXECUTE FUNCTION invalidate_control_reviews();
      CREATE TRIGGER control_asset_invalidated AFTER UPDATE ON assets
        FOR EACH ROW EXECUTE FUNCTION invalidate_control_reviews();
      CREATE FUNCTION control_summary(target uuid) RETURNS bigint LANGUAGE plpgsql
        SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
      DECLARE a record;
      BEGIN
        SELECT * INTO a FROM control_actor();
        IF a.usr IS NULL OR NOT control_permitted('summary') THEN
          RAISE EXCEPTION 'CONTROL_FORBIDDEN' USING ERRCODE='42501';
        END IF;
        IF NOT EXISTS(SELECT FROM assets WHERE organization_id=a.org AND id=target) THEN
          RAISE EXCEPTION 'CONTROL_NOT_FOUND' USING ERRCODE='23514',CONSTRAINT='control_reference';
        END IF;
        RETURN (SELECT count(*) FROM control_review_requests
          WHERE organization_id=a.org AND asset_id=target);
      END $$;
    """)
    for table in ("control_review_requests", "control_review_projections", "control_review_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON {table} TO sentinelai_runtime")
        expression = "organization_id::text=current_setting('app.organization_id',true)"
        visible = "control_visible(presenter_id)"
        if table == "control_review_events":
            visible = (
                "EXISTS(SELECT FROM control_review_requests r WHERE "
                f"(r.organization_id,r.id)=({table}.organization_id,{table}.review_id))"
            )
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT TO sentinelai_runtime "
            f"USING({expression} AND {visible})"
        )
        if table != "control_review_projections":
            op.execute(
                f"CREATE POLICY {table}_insert ON {table} FOR INSERT TO sentinelai_runtime "
                f"WITH CHECK({expression})"
            )
    op.execute("""
      GRANT INSERT(organization_id,id,asset_id,evidence_id,evidence_version,asset_version,
        renews_review_id) ON control_review_requests TO sentinelai_runtime;
      GRANT INSERT(organization_id,id,review_id,kind,expected_version,reason_code,
        checklist_version,checklist) ON control_review_events TO sentinelai_runtime;
      REVOKE ALL ON FUNCTION control_actor(),control_permitted(text),control_visible(uuid),
        control_compatible(assets,control_review_requests),control_admitted(control_review_requests),
        control_json(jsonb),control_log(control_review_events),guard_control_request(),
        guard_control_event(),apply_control_event(),begin_control_review(),
        invalidate_control_reviews(),control_summary(uuid) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION control_visible(uuid),control_summary(uuid) TO sentinelai_runtime;
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT FROM control_review_requests)")):
        raise RuntimeError("Downgrade 09 refuses control-review history; preserve the database.")
    op.execute("""
      DROP TRIGGER control_admission_invalidated ON asset_admission_current;
      DROP TRIGGER control_asset_invalidated ON assets;
      DROP TABLE control_review_events,control_review_projections,control_review_requests CASCADE;
      DROP FUNCTION IF EXISTS invalidate_control_reviews(),begin_control_review(),
        apply_control_event(),guard_control_event(),guard_control_request(),
        control_json(jsonb),control_visible(uuid),control_permitted(text),control_actor(),
        control_summary(uuid);
      ALTER TABLE security_audit_events DROP CONSTRAINT uq_control_audit_identity;
      ALTER TABLE evidence_versions DROP CONSTRAINT uq_control_evidence_binding;
      DELETE FROM role_permissions WHERE permission_id IN
        (SELECT id FROM permissions WHERE resource='control');
      DELETE FROM permissions WHERE resource='control';
    """)
