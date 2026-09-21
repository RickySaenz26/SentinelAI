"""Domain policy for tenant membership role assignment and owner safety."""

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.authorization.policy import (
    TENANT_ASSIGNABLE_ROLES,
    ActorContext,
    assert_role_assignment_allowed,
)
from app.core.errors import ApplicationError
from app.platform.database.models import Membership, Role


def assert_membership_change_allowed(
    actor: ActorContext,
    *,
    action: str,
    organization_id: UUID,
    target_user_id: UUID,
    current_role_code: str | None = None,
    new_role_code: str | None = None,
) -> None:
    """Closed tenant policy, including the target's existing privilege and identity.

    Self-updates follow the same hierarchy; last-owner protection is applied under
    the organization lock. Creating an already-present self-membership is rejected.
    A platform membership is immutable through every tenant action, including self.
    """
    if organization_id != actor.organization_id:
        raise ApplicationError(
            "ORGANIZATION_NOT_FOUND", "El recurso solicitado no está disponible.", 404
        )
    permission = {
        "create": "membership:invite",
        "update": "membership:update",
        "revoke": "membership:revoke",
    }.get(action)
    if permission is None:
        raise ValueError("Unsupported membership operation.")
    actor.require(permission)
    manageable = TENANT_ASSIGNABLE_ROLES.get(actor.role_code, frozenset())
    if action != "create" and current_role_code not in manageable:
        raise ApplicationError("FORBIDDEN", "El recurso solicitado no está disponible.", 403)
    if new_role_code is not None:
        assert_role_assignment_allowed(actor, new_role_code)
    if action == "create":
        if new_role_code is None:
            raise ApplicationError("FORBIDDEN", "El recurso solicitado no está disponible.", 403)
        if actor.user_id == target_user_id:
            raise ApplicationError("MEMBERSHIP_EXISTS", "La membresía ya existe.", 409)


def resolve_assignable_role(
    session: Session, actor: ActorContext, organization_id: UUID, role_code: str
) -> Role:
    """Resolve a system/tenant role only in this tenant and enforce the domain policy."""
    assert_role_assignment_allowed(actor, role_code)
    role = session.scalar(
        select(Role)
        .where(
            Role.code == role_code,
            (Role.organization_id.is_(None)) | (Role.organization_id == organization_id),
        )
        .order_by(Role.is_system.desc(), Role.id)
    )
    if role is None:
        raise ApplicationError("ROLE_NOT_FOUND", "El recurso solicitado no está disponible.", 404)
    return role


def lock_organization(session: Session, organization_id: UUID) -> None:
    """First lock for tenant writes: organization advisory, target row, audit lock.

    All membership writers (including onboarding) take this before row locks.
    Reentrant acquisition by last-owner validation is safe within the transaction.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:organization_id, 1))"),
        {"organization_id": str(organization_id)},
    )


def protect_last_owner(
    session: Session,
    membership: Membership,
    current_role: Role,
    proposed_role: Role,
    proposed_status: str,
) -> None:
    """Serialize owner changes per organization before deciding whether one remains."""
    if (
        membership.status != "active"
        or current_role.code != "org_owner"
        or (proposed_role.code == "org_owner" and proposed_status == "active")
    ):
        return
    lock_organization(session, membership.organization_id)
    owner_count = session.scalar(
        select(func.count())
        .select_from(Membership)
        .join(Role, Role.id == Membership.role_id)
        .where(
            Membership.organization_id == membership.organization_id,
            Membership.status == "active",
            Membership.deleted_at.is_(None),
            Role.code == "org_owner",
        )
    )
    if owner_count is not None and owner_count <= 1:
        raise ApplicationError(
            "LAST_OWNER_PROTECTED", "La organización debe conservar un owner activo.", 409
        )
