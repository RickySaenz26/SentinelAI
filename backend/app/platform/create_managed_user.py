"""Trusted-terminal onboarding command for managed users; never prints credentials."""

import argparse
import getpass
from uuid import UUID

from sqlalchemy import select

from app.authorization.membership_policy import lock_organization
from app.identity.validation import normalize_email, validate_password
from app.platform.crypto import hash_password
from app.platform.database.models import Membership, Organization, PasswordCredential, Role, User
from app.platform.database.session import get_session_factory, set_organization_context
from app.platform.outbox import emit_event
from app.security_audit.service import record_event

MANAGED_ROLES = frozenset({"org_owner", "security_manager", "analyst", "viewer", "auditor"})


def create_managed_user(
    email: str, display_name: str, organization_id: UUID, role_code: str
) -> None:
    if role_code not in MANAGED_ROLES:
        raise ValueError("Role is not allowed for managed onboarding.")
    normalized_email = normalize_email(email)
    if not display_name.strip():
        raise ValueError("Display name is required.")
    password = getpass.getpass("Managed user password: ")
    confirmation = getpass.getpass("Confirm managed user password: ")
    if password != confirmation:
        raise ValueError("Password confirmation does not match.")
    validate_password(password)
    session = get_session_factory()()
    try:
        set_organization_context(session, organization_id)
        lock_organization(session, organization_id)
        organization = session.get(Organization, organization_id)
        if (
            organization is None
            or organization.deleted_at is not None
            or organization.status != "active"
        ):
            raise ValueError("Organization is not active or does not exist.")
        role = session.scalar(
            select(Role).where(Role.code == role_code, Role.organization_id.is_(None))
        )
        if role is None:
            raise ValueError("Role is not available for managed onboarding.")
        if session.scalar(
            select(User.id).where(User.email == normalized_email, User.deleted_at.is_(None))
        ):
            raise ValueError("A managed user with this email already exists.")
        user = User(email=normalized_email, display_name=display_name.strip())
        session.add(user)
        session.flush()
        session.add(PasswordCredential(user_id=user.id, password_hash=hash_password(password)))
        membership = Membership(organization_id=organization_id, user_id=user.id, role_id=role.id)
        session.add(membership)
        session.flush()
        record_event(
            session,
            request_id="managed-user-local",
            action="managed_user.created",
            resource_type="user",
            resource_id=user.id,
            outcome="success",
            organization_id=organization_id,
            actor_type="system",
        )
        emit_event(
            session,
            organization_id=organization_id,
            aggregate_type="user",
            aggregate_id=user.id,
            event_type="managed_user.created",
            idempotency_key=f"managed_user.created:{user.id}:1",
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        password = ""
        confirmation = ""
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a managed SentinelAI user from a trusted terminal."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--organization-id", required=True, type=UUID)
    parser.add_argument("--role-code", required=True)
    args = parser.parse_args()
    create_managed_user(args.email, args.display_name, args.organization_id, args.role_code)
    print("Managed user created. No credentials were printed.")


if __name__ == "__main__":
    main()
