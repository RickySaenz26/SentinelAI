from dataclasses import dataclass
from uuid import UUID

from app.core.errors import ApplicationError


@dataclass(frozen=True, slots=True)
class ActorContext:
    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    session_id: UUID
    role_code: str
    permissions: frozenset[str]
    csrf_token: str

    def require(self, permission: str) -> None:
        if permission not in self.permissions and "platform:admin" not in self.permissions:
            raise ApplicationError("FORBIDDEN", "El recurso solicitado no está disponible.", 403)


TENANT_ASSIGNABLE_ROLES: dict[str, frozenset[str]] = {
    "platform_admin": frozenset({"org_owner", "security_manager", "analyst", "viewer", "auditor"}),
    "org_owner": frozenset({"org_owner", "security_manager", "analyst", "viewer", "auditor"}),
    "security_manager": frozenset({"analyst", "viewer", "auditor"}),
}


def assert_role_assignment_allowed(actor: ActorContext, role_code: str) -> None:
    """Tenant membership endpoints never assign the reserved platform administrator role."""
    if role_code == "platform_admin":
        raise ApplicationError("FORBIDDEN", "El recurso solicitado no está disponible.", 403)
    allowed = TENANT_ASSIGNABLE_ROLES.get(actor.role_code, frozenset())
    if role_code not in allowed:
        raise ApplicationError("FORBIDDEN", "El recurso solicitado no está disponible.", 403)
