"""Explicit, local-only bootstrap command for the initial platform administrator."""

import argparse
import getpass
import re
from uuid import uuid4

from sqlalchemy import select, text

from app.authorization.membership_policy import lock_organization
from app.identity.validation import normalize_email, validate_password
from app.platform.crypto import hash_password
from app.platform.database.models import Membership, Organization, PasswordCredential, Role, User
from app.platform.database.session import get_session_factory, set_organization_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug or len(slug) > 80:
        raise ValueError("Organization name cannot produce a valid slug.")
    return slug


def bootstrap(email: str, display_name: str, organization_name: str) -> None:
    password = getpass.getpass("Initial administrator password: ")
    confirmation = getpass.getpass("Confirm initial administrator password: ")
    if password != confirmation:
        raise ValueError("Password confirmation does not match.")
    validate_password(password)
    normalized_email = normalize_email(email)
    if not display_name.strip() or not organization_name.strip():
        raise ValueError("Display name and organization name are required.")
    session = get_session_factory()()
    try:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('sentinelai-bootstrap', 2))")
        )
        if session.scalar(select(User.id).limit(1)) is not None:
            raise ValueError("Bootstrap is allowed only on an empty identity database.")
        organization = Organization(
            id=uuid4(), name=organization_name, slug=slugify(organization_name)
        )
        session.add(organization)
        set_organization_context(session, organization.id)
        lock_organization(session, organization.id)
        admin_role = session.scalar(
            select(Role).where(Role.code == "platform_admin", Role.is_system.is_(True))
        )
        if admin_role is None:
            raise RuntimeError("System roles are missing; run Alembic migrations first.")
        user = User(email=normalized_email, display_name=display_name.strip())
        session.add(user)
        session.flush()
        session.add(PasswordCredential(user_id=user.id, password_hash=hash_password(password)))
        membership = Membership(
            organization_id=organization.id, user_id=user.id, role_id=admin_role.id
        )
        session.add(membership)
        session.flush()
        record_event(
            session,
            request_id="bootstrap-local",
            action="platform.bootstrap",
            resource_type="user",
            resource_id=user.id,
            outcome="success",
            organization_id=organization.id,
            actor_user_id=user.id,
        )
        emit_event(
            session,
            organization_id=organization.id,
            aggregate_type="user",
            aggregate_id=user.id,
            event_type="platform.bootstrap",
            idempotency_key=f"platform.bootstrap:{organization.id}",
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap the first SentinelAI platform administrator."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--organization-name", required=True)
    args = parser.parse_args()
    bootstrap(args.email, args.display_name, args.organization_name)
    print("Bootstrap completed. No credentials were printed.")


if __name__ == "__main__":
    main()
