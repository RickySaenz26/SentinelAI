"""Explicit operator CLI. Does not provision credentials, tenants or contact targets."""

import argparse
import json
import os
import re
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.assets.authority import current_policy
from app.assets.configuration import get_lab_policy
from app.assets.policy import LabPolicy
from app.authorization.membership_policy import lock_organization
from app.core.errors import ApplicationError
from app.platform.database.session import set_organization_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event


class PolicyPublisher:
    def __init__(self, sessions, organization_id: UUID):
        self.sessions, self.organization_id = sessions, organization_id

    def scope(self, session):
        safe = session.scalar(
            text("""
            SELECT current_user=session_user AND NOT rolsuper AND NOT rolbypassrls
              AND NOT rolcreatedb AND NOT rolcreaterole
              AND pg_has_role(current_user,'sentinelai_policy_publisher','member')
              AND NOT pg_has_role(current_user,'sentinelai_runtime','member')
              AND NOT pg_has_role(current_user,'sentinelai_evidence_maintenance','member')
            FROM pg_roles WHERE rolname=current_user
        """)
        )
        if not safe or not isinstance(self.organization_id, UUID):
            raise ValueError("Dedicated restricted policy publisher and tenant required.")
        if not session.scalar(
            text(
                "SELECT EXISTS(SELECT FROM policy_publisher_tenants "
                "WHERE role_name=current_user AND organization_id=:org)"
            ),
            {"org": self.organization_id},
        ):
            raise ValueError("Publisher tenant not provisioned.")
        set_organization_context(session, self.organization_id)

    def inspect(self):
        with self.sessions() as session:
            self.scope(session)
            published = current_policy(session, self.organization_id)
            return {
                "sequence": published.sequence if published else 0,
                "revision_id": str(published.revision_id) if published else None,
                "policy_hash": published.policy.fingerprint if published else None,
            }

    @staticmethod
    def commit(session):
        # Explicit seam for lost-acknowledgement tests; never compensate an uncertain commit.
        session.commit()

    def publish(
        self, policy: LabPolicy, *, expected_sequence: int, publication_id: UUID, provenance: str
    ):
        policy = LabPolicy.model_validate_json(policy.model_dump_json())
        if (
            type(expected_sequence) is not int
            or expected_sequence < 0
            or not isinstance(publication_id, UUID)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .:_-]{0,159}", provenance) is None
        ):
            raise ValueError("Explicit expected revision, UUID and bounded provenance required.")
        with self.sessions() as session:
            self.scope(session)
            lock_organization(session, self.organization_id)
            previous = (
                session.execute(
                    text(
                        "SELECT * FROM lab_policy_revisions WHERE organization_id=:org AND id=:id"
                    ),
                    {"org": self.organization_id, "id": publication_id},
                )
                .mappings()
                .first()
            )
            if previous is not None:
                if (
                    previous["sequence"] != expected_sequence + 1
                    or previous["snapshot"] != policy.model_dump_json()
                    or previous["provenance"] != provenance
                    or previous["publisher"] != session.scalar(text("SELECT current_user"))
                ):
                    raise ApplicationError("IDEMPOTENCY_CONFLICT", "Publicación distinta.", 409)
                # Historical receipt only: never reactivates this revision.
                return {
                    "revision_id": str(previous["id"]),
                    "sequence": previous["sequence"],
                    "replayed": True,
                }
            current = current_policy(session, self.organization_id)
            if (current.sequence if current else 0) != expected_sequence:
                raise ApplicationError("POLICY_REVISION_CONFLICT", "La política cambió.", 409)
            session.execute(
                text(
                    "INSERT INTO lab_policy_revisions "
                    "(organization_id,id,sequence,snapshot,policy_hash,provenance) "
                    "VALUES(:org,:id,:sequence,:snapshot,:hash,:provenance)"
                ),
                {
                    "org": self.organization_id,
                    "id": publication_id,
                    "sequence": expected_sequence + 1,
                    "snapshot": policy.model_dump_json(),
                    "hash": policy.fingerprint,
                    "provenance": provenance,
                },
            )
            details = {
                "sequence": expected_sequence + 1,
                "policy_hash": policy.fingerprint,
                "publisher": session.scalar(text("SELECT current_user")),
                "provenance": provenance,
            }
            record_event(
                session,
                request_id=str(publication_id),
                action="lab_policy.published",
                resource_type="lab_policy",
                resource_id=publication_id,
                outcome="success",
                organization_id=self.organization_id,
                actor_type="system",
                details=details,
            )
            emit_event(
                session,
                organization_id=self.organization_id,
                aggregate_type="lab_policy",
                aggregate_id=publication_id,
                event_type="lab_policy.published",
                idempotency_key=f"lab_policy:{publication_id}",
                payload=details,
            )
            self.commit(session)
            return {
                "revision_id": str(publication_id),
                "sequence": expected_sequence + 1,
                "replayed": False,
            }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", type=UUID, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--expected-sequence", type=int)
    parser.add_argument("--publication-id", type=UUID)
    parser.add_argument("--provenance")
    args = parser.parse_args(argv)
    policy = get_lab_policy()
    if policy is None:
        parser.error("Valid explicit LAB_ASSET_POLICY_JSON is required; no default policy.")
    url = os.environ.get("LAB_POLICY_PUBLISHER_DATABASE_URL")
    if not url:
        parser.error("Separate publisher database credential required.")
    engine = create_engine(url)
    try:
        publisher = PolicyPublisher(sessionmaker(engine), args.organization)
        print(
            json.dumps(
                {
                    "current": publisher.inspect(),
                    "candidate_hash": policy.fingerprint,
                    "allowed_count": len(policy.allowed_targets),
                    "excluded_count": len(policy.excluded_targets),
                }
            )
        )
        if args.publish:
            print(
                json.dumps(
                    publisher.publish(
                        policy,
                        expected_sequence=args.expected_sequence,
                        publication_id=args.publication_id,
                        provenance=args.provenance or "",
                    )
                )
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
