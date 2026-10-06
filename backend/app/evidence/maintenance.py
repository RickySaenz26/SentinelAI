"""Explicit local reconciliation. Inspection is default; no orphan promotion or retention purge."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.authorization.membership_policy import lock_organization
from app.evidence.configuration import configured_storage
from app.evidence.coordination import coordinate
from app.evidence.crypto import MAX_ENVELOPE_BYTES
from app.evidence.errors import UnsafeStorage
from app.evidence.service import EvidenceService, receipt_from
from app.platform.database.session import set_organization_context


class EvidenceMaintenance:
    def __init__(self, sessions, storage, *, organization_id: UUID):
        self.sessions, self.storage, self.organization_id = sessions, storage, organization_id

    def scope(self, session):
        safe = session.scalar(
            text("""
            SELECT NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb
              AND current_user=session_user
              AND pg_has_role(current_user,'sentinelai_evidence_maintenance','member')
              AND NOT pg_has_role(current_user,'sentinelai_runtime','member')
            FROM pg_roles WHERE rolname=current_user
        """)
        )
        if not safe or not isinstance(self.organization_id, UUID):
            raise UnsafeStorage("Restricted evidence maintenance credentials and tenant required.")
        authorized = session.scalar(
            text(
                "SELECT EXISTS(SELECT FROM evidence_maintenance_tenants "
                "WHERE role_name=current_user AND organization_id=:org)"
            ),
            {"org": self.organization_id},
        )
        if not authorized:
            raise UnsafeStorage("Operational tenant is not provisioned for this credential.")
        set_organization_context(session, self.organization_id)

    def inspect(self):
        with self.sessions() as session:
            self.scope(session)
            rows = [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT *, expires_at <= clock_timestamp() AS expired "
                        "FROM evidence_operations "
                        "WHERE organization_id=:org ORDER BY created_at,id"
                    ),
                    {"org": self.organization_id},
                ).mappings()
            ]
        # Advisory inventory only: other tenants' objects are also unknown to this scope.
        known = {
            name
            for row in rows
            if row["object_id"] is not None
            for name in self.storage.names(receipt_from(row))
        }
        unknown = sum(
            name not in known
            for name in os.listdir(self.storage.directory.fd)
            if name.endswith((".evidence", ".tmp"))
        )
        return rows, unknown

    def claim(self, identifier):
        with self.sessions.begin() as session:
            self.scope(session)
            lock_organization(session, self.organization_id)
            EvidenceService.quota(session, self.organization_id)
            row = EvidenceService.operation(session, self.organization_id, identifier)
            referenced = session.scalar(
                text("SELECT EXISTS(SELECT FROM evidence_versions WHERE operation_id=:id)"),
                {"id": identifier},
            )
            if referenced or row["state"] in ("committed", "aborted"):
                return None
            if row["state"] != "reclaimed":
                if row["expires_at"] > session.scalar(text("SELECT clock_timestamp()")):
                    return None
                session.execute(
                    text("UPDATE evidence_operations SET state='reclaimed' WHERE id=:id"),
                    {"id": identifier},
                )
            return row

    def clean_owned(self, row):
        if row["object_id"] is None:
            return
        receipt = receipt_from(row)
        temporary, final = self.storage.names(receipt)
        directory = self.storage.directory
        with directory.lock(exclusive=True):
            entries = os.listdir(directory.fd)
            if final in entries:
                self.storage.validate(receipt, self.storage.read_raw(final, maximum_links=2))
                if temporary in entries:
                    first = os.stat(temporary, dir_fd=directory.fd, follow_symlinks=False)
                    second = os.stat(final, dir_fd=directory.fd, follow_symlinks=False)
                    if (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino):
                        raise UnsafeStorage(
                            "Conflicting temporary object; preserve for inspection."
                        )
                    os.unlink(temporary, dir_fd=directory.fd)
                os.unlink(final, dir_fd=directory.fd)
                directory.sync()
                return
        # Stage 1's audited abort handles known incomplete temporaries, including size zero.
        self.storage.discard_temporary(receipt)
        # A previous attempt may have unlinked the final and failed its directory fsync.
        # Absence alone is not a successful durability barrier before releasing the quota.
        directory.sync()

    def release(self, identifier):
        with self.sessions.begin() as session:
            self.scope(session)
            lock_organization(session, self.organization_id)
            EvidenceService.quota(session, self.organization_id)
            row = EvidenceService.operation(session, self.organization_id, identifier)
            if row["state"] != "reclaimed":
                raise UnsafeStorage("Reconciliation fence is required.")
            session.execute(
                text("UPDATE evidence_operations SET state='aborted' WHERE id=:id"),
                {"id": identifier},
            )
            session.execute(
                text(
                    "UPDATE evidence_quotas SET reserved_objects=reserved_objects-1,"
                    "reserved_bytes=reserved_bytes-:size WHERE organization_id=:org"
                ),
                {"size": MAX_ENVELOPE_BYTES, "org": self.organization_id},
            )

    def run(self, *, execute=False):
        rows, unknown = self.inspect()
        candidates = [
            row["id"]
            for row in rows
            if row["state"] == "reclaimed"
            or (row["state"] in ("reserved", "prepared") and row["expired"])
        ]
        cleaned = 0
        if execute:
            with coordinate(self.storage):
                for identifier in candidates:
                    row = self.claim(identifier)
                    if row is not None:
                        # claim's commit completed; no DB locks or transaction during I/O.
                        self.clean_owned(row)
                        self.release(identifier)
                        cleaned += 1
        return {
            "candidates": len(candidates),
            "cleaned": cleaned,
            "unknown_preserved": unknown,
            "execute": execute,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect local evidence reservations; no promotion."
    )
    parser.add_argument("--organization", type=UUID, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    engine = None
    try:
        engine = create_engine(os.environ["LAB_EVIDENCE_MAINTENANCE_DATABASE_URL"])
        with configured_storage(os.environ, repository_root=args.repository_root) as storage:
            result = EvidenceMaintenance(
                sessionmaker(engine), storage, organization_id=args.organization
            ).run(execute=args.execute)
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print("Evidence maintenance failed; preserve objects and inspect configuration.")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
