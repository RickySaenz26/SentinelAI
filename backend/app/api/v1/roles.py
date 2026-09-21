from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_actor
from app.authorization.policy import ActorContext
from app.platform.database.models import Permission, Role, RolePermission
from app.platform.database.session import get_db_session, set_organization_context

router = APIRouter()


@router.get("/roles")
def list_roles(
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    actor.require("role:read")
    set_organization_context(session, actor.organization_id)
    roles = session.scalars(select(Role).order_by(Role.code)).all()
    items: list[dict[str, object]] = []
    for role in roles:
        permissions = sorted(
            session.scalars(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
            ).all()
        )
        items.append(
            {
                "id": str(role.id),
                "code": role.code,
                "name": role.name,
                "is_system": role.is_system,
                "permissions": permissions,
            }
        )
    return {"items": items}
