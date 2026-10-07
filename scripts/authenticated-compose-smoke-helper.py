"""Ephemeral database setup and evidence checks for the deployed Compose smoke."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.identity.validation import normalize_email, validate_password
from app.platform.crypto import hash_password
from app.platform.database.models import (
    Membership,
    Organization,
    PasswordCredential,
    Role,
    SecurityAuditEvent,
    User,
)
from app.platform.database.session import get_session_factory, set_organization_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event, verify_chain


@dataclass(frozen=True)
class SeedIdentity:
    organization_name: str
    organization_slug: str
    email: str
    display_name: str
    password: str
    role_code: str


def _require_ephemeral_smoke_database() -> None:
    settings = get_settings()
    database_url = settings.database_url_value
    database_name = make_url(database_url).database if database_url else None
    if settings.environment != "test" or not database_name:
        raise RuntimeError("Smoke helper requires the isolated test environment.")
    if not database_name.startswith("sentinelai_smoke_"):
        raise RuntimeError("Smoke helper refused a non-smoke database.")


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required smoke field: {name}")
    return value.strip()


def _identity(payload: dict[str, object], prefix: str, role_code: str) -> SeedIdentity:
    password = _required_string(payload, f"{prefix}_password")
    validate_password(password)
    return SeedIdentity(
        organization_name=_required_string(payload, f"{prefix}_organization_name"),
        organization_slug=_required_string(payload, f"{prefix}_organization_slug"),
        email=normalize_email(_required_string(payload, f"{prefix}_email")),
        display_name=_required_string(payload, f"{prefix}_display_name"),
        password=password,
        role_code=role_code,
    )


def _seed_identity(session: Session, identity: SeedIdentity) -> tuple[UUID, UUID, UUID]:
    organization = Organization(
        id=uuid4(), name=identity.organization_name, slug=identity.organization_slug
    )
    set_organization_context(session, organization.id)
    session.add(organization)
    session.flush()

    role = session.scalar(
        select(Role).where(
            Role.code == identity.role_code,
            Role.is_system.is_(True),
            Role.organization_id.is_(None),
        )
    )
    if role is None:
        raise RuntimeError("Required system role is unavailable.")

    user = User(email=identity.email, display_name=identity.display_name)
    session.add(user)
    session.flush()
    session.add(PasswordCredential(user_id=user.id, password_hash=hash_password(identity.password)))
    membership = Membership(
        organization_id=organization.id,
        user_id=user.id,
        role_id=role.id,
    )
    session.add(membership)
    session.flush()
    record_event(
        session,
        request_id="compose-smoke-seed",
        action="smoke.identity.seeded",
        resource_type="user",
        resource_id=user.id,
        outcome="success",
        organization_id=organization.id,
        actor_type="system",
    )
    emit_event(
        session,
        organization_id=organization.id,
        aggregate_type="user",
        aggregate_id=user.id,
        event_type="smoke.identity.seeded",
        idempotency_key=f"smoke.identity.seeded:{user.id}",
    )
    session.commit()
    return organization.id, user.id, membership.id


def seed() -> None:
    _require_ephemeral_smoke_database()
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Smoke seed input must be a JSON object.")
    identity_a = _identity(payload, "a", "platform_admin")
    identity_b = _identity(payload, "b", "viewer")

    with get_session_factory()() as session:
        existing_users = session.scalar(select(func.count()).select_from(User))
        if existing_users != 0:
            raise RuntimeError("Smoke seed requires a clean identity database.")
        org_a, user_a, membership_a = _seed_identity(session, identity_a)
        org_b, user_b, membership_b = _seed_identity(session, identity_b)
        if payload.get("evidence_owner_email"):
            set_organization_context(session, org_a)
            owner = User(
                email=normalize_email(payload["evidence_owner_email"]),
                display_name="Ephemeral evidence presenter",
            )
            session.add(owner)
            session.flush()
            password = _required_string(payload, "evidence_owner_password")
            validate_password(password)
            session.add(PasswordCredential(user_id=owner.id, password_hash=hash_password(password)))
            role = session.scalar(select(Role).where(Role.code == "org_owner", Role.is_system))
            session.add(Membership(organization_id=org_a, user_id=owner.id, role_id=role.id))
            session.commit()

    result = {
        "organization_a_id": str(org_a),
        "organization_b_id": str(org_b),
        "user_a_id": str(user_a),
        "user_b_id": str(user_b),
        "membership_a_id": str(membership_a),
        "membership_b_id": str(membership_b),
    }
    print(f"SMOKE_SEED_RESULT={json.dumps(result, sort_keys=True)}")


def _assert_runtime_role(session: Session) -> None:
    role = session.execute(
        text(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
    ).one()
    if role.current_user != "sentinelai_runtime" or role.rolsuper or role.rolbypassrls:
        raise AssertionError("Smoke database connection is not the restricted runtime role.")


def _assert_rls_visibility(organization_a: UUID, organization_b: UUID) -> None:
    with get_session_factory()() as session:
        _assert_runtime_role(session)
        set_organization_context(session, organization_a)
        visible_ids = set(session.scalars(select(Organization.id)).all())
        if organization_a not in visible_ids or organization_b in visible_ids:
            raise AssertionError("Runtime tenant visibility did not match the active RLS context.")
        foreign = session.scalar(select(Organization.id).where(Organization.id == organization_b))
        if foreign is not None:
            raise AssertionError("Foreign organization was visible through the runtime role.")

    with get_session_factory()() as session:
        set_organization_context(session, organization_a)
        try:
            session.execute(text("SET LOCAL row_security = off"))
            session.scalars(select(Organization.id)).all()
        except DBAPIError as error:
            session.rollback()
            if getattr(error.orig, "sqlstate", None) != "42501":
                raise AssertionError("Unexpected error while proving RLS bypass denial.") from error
        else:
            raise AssertionError("Runtime role unexpectedly bypassed row-level security.")


def _assert_audit_evidence(organization_a: UUID, user_a: UUID) -> None:
    with get_session_factory()() as session:
        set_organization_context(session, organization_a)
        event = session.scalar(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.organization_id == organization_a,
                SecurityAuditEvent.actor_user_id == user_a,
                SecurityAuditEvent.action == "organization.updated",
                SecurityAuditEvent.resource_id == organization_a,
                SecurityAuditEvent.outcome == "success",
            )
            .order_by(SecurityAuditEvent.sequence.desc())
        )
        if event is None or event.hash_version != 3 or len(event.event_hash) != 64:
            raise AssertionError("Expected chained organization audit evidence is absent.")
        chain_valid, invalid_event = verify_chain(session, organization_a)
        if not chain_valid or invalid_event is not None:
            raise AssertionError("Organization audit chain verification failed.")
        event_id = event.id

    with get_session_factory()() as session:
        set_organization_context(session, organization_a)
        try:
            session.execute(
                text("UPDATE security_audit_events SET outcome = 'denied' WHERE id = :event_id"),
                {"event_id": event_id},
            )
            session.flush()
        except DBAPIError as error:
            session.rollback()
            if getattr(error.orig, "sqlstate", None) != "42501":
                raise AssertionError(
                    "Unexpected error while proving audit immutability."
                ) from error
        else:
            raise AssertionError("Runtime role unexpectedly modified immutable audit evidence.")


def verify(organization_a: UUID, organization_b: UUID, user_a: UUID) -> None:
    _require_ephemeral_smoke_database()
    _assert_rls_visibility(organization_a, organization_b)
    _assert_audit_evidence(organization_a, user_a)
    print("SMOKE_DATABASE_EVIDENCE=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("seed")
    evidence_parser = subcommands.add_parser("verify-evidence")
    evidence_parser.add_argument("--organization-a", required=True, type=UUID)
    verify_parser = subcommands.add_parser("verify")
    verify_parser.add_argument("--organization-a", required=True, type=UUID)
    verify_parser.add_argument("--organization-b", required=True, type=UUID)
    verify_parser.add_argument("--user-a", required=True, type=UUID)
    args = parser.parse_args()
    try:
        if args.command == "seed":
            seed()
        elif args.command == "verify-evidence":
            _require_ephemeral_smoke_database()
            with get_session_factory()() as session:
                set_organization_context(session, args.organization_a)
                for query in (
                    "SELECT count(*) FROM evidence_versions",
                    "SELECT count(*) FROM evidence_operations WHERE state='committed'",
                    "SELECT count(*) FROM security_audit_events WHERE action='evidence.stored'",
                    "SELECT count(*) FROM outbox_events WHERE event_type='evidence.stored'",
                    "SELECT count(*) FROM security_audit_events "
                    "WHERE action='evidence.content_read'",
                ):
                    assert session.scalar(text(query)) == 1
                assert verify_chain(session, args.organization_a) == (True, None)
            print("HTTPS_EVIDENCE_RECORDS=PASS")
        else:
            verify(args.organization_a, args.organization_b, args.user_a)
    except Exception as error:
        print(f"SMOKE_HELPER_ERROR={type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
