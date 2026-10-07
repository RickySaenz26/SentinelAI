"""Explicit evidence API permissions; no new data, grants or verification states."""

import sqlalchemy as sa

from alembic import op

revision = "20261006_07"
down_revision = "20261005_06"
branch_labels = None
depends_on = None

PERMISSIONS = {
    "summary": ("org_owner", "security_manager", "analyst", "auditor", "viewer", "platform_admin"),
    "metadata": ("org_owner", "security_manager", "auditor"),
    "read": ("org_owner", "security_manager", "auditor"),
    "read_own": ("analyst",),
}


def upgrade() -> None:
    for action, roles in PERMISSIONS.items():
        op.get_bind().execute(
            sa.text(
                "INSERT INTO permissions(id,code,resource,action) "
                "VALUES(gen_random_uuid(),:code,'evidence',:action)"
            ),
            {"code": f"evidence:{action}", "action": action},
        )
        for role in roles:
            op.get_bind().execute(
                sa.text(
                    "INSERT INTO role_permissions(role_id,permission_id) "
                    "SELECT r.id,p.id FROM roles r CROSS JOIN permissions p "
                    "WHERE r.organization_id IS NULL AND r.code=:role AND p.code=:code"
                ),
                {"role": role, "code": f"evidence:{action}"},
            )
    op.execute(
        "INSERT INTO role_permissions(role_id,permission_id) "
        "SELECT r.id,p.id FROM roles r CROSS JOIN permissions p "
        "WHERE r.organization_id IS NULL AND r.code='analyst' AND p.code='evidence:write'"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE role_id IN "
        "(SELECT id FROM roles WHERE organization_id IS NULL AND code='analyst') "
        "AND permission_id IN (SELECT id FROM permissions WHERE code='evidence:write')"
    )
    for action in PERMISSIONS:
        op.get_bind().execute(
            sa.text(
                "DELETE FROM role_permissions WHERE permission_id IN "
                "(SELECT id FROM permissions WHERE code=:code)"
            ),
            {"code": f"evidence:{action}"},
        )
        op.get_bind().execute(
            sa.text("DELETE FROM permissions WHERE code=:code"), {"code": f"evidence:{action}"}
        )
