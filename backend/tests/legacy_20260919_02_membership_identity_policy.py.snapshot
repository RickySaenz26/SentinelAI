"""Permit a user to resolve only their own membership before tenant activation."""

from alembic import op

revision = "20260919_02"
down_revision = "20260919_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS memberships_tenant_isolation ON memberships")
    op.execute(
        """
        CREATE POLICY memberships_tenant_isolation ON memberships
        USING (
            organization_id::text = current_setting('app.organization_id', true)
            OR user_id::text = current_setting('app.user_id', true)
        )
        WITH CHECK (organization_id::text = current_setting('app.organization_id', true))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS memberships_tenant_isolation ON memberships")
    op.execute(
        """
        CREATE POLICY memberships_tenant_isolation ON memberships
        USING (organization_id::text = current_setting('app.organization_id', true))
        WITH CHECK (organization_id::text = current_setting('app.organization_id', true))
        """
    )
