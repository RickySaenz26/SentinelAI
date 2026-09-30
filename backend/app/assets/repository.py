"""SQL adapter. All inventory queries include the trusted tenant explicitly."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.platform.database.models import Asset


class AssetRepository:
    def __init__(self, session: Session, organization_id: UUID):
        self.session = session
        self.organization_id = organization_id

    def get(self, asset_id: UUID, *, lock: bool = False) -> Asset | None:
        query = select(Asset).where(
            Asset.organization_id == self.organization_id, Asset.id == asset_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def active_count(self) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(Asset)
                .where(Asset.organization_id == self.organization_id, Asset.deleted_at.is_(None))
            )
            or 0
        )

    def target_exists(self, target: str) -> bool:
        return (
            self.session.scalar(
                select(Asset.id).where(
                    Asset.organization_id == self.organization_id,
                    Asset.canonical_target == target,
                    Asset.deleted_at.is_(None),
                )
            )
            is not None
        )

    def create(
        self, *, target: str, display_name: str, criticality: str, policy_hash: str
    ) -> Asset:
        asset = Asset(
            organization_id=self.organization_id,
            canonical_target=target,
            display_name=display_name,
            criticality=criticality,
            policy_hash=policy_hash,
        )
        self.session.add(asset)
        self.session.flush()
        return asset

    def page(
        self,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
        status: str,
        criticality: str | None,
        query: str | None,
    ) -> list[Asset]:
        statement = select(Asset).where(Asset.organization_id == self.organization_id)
        if status == "active":
            statement = statement.where(Asset.deleted_at.is_(None))
        elif status == "archived":
            statement = statement.where(Asset.deleted_at.is_not(None))
        if criticality:
            statement = statement.where(Asset.criticality == criticality)
        if query:
            statement = statement.where(Asset.display_name.icontains(query, autoescape=True))
        if after:
            statement = statement.where(tuple_(Asset.created_at, Asset.id) > after)
        return list(
            self.session.scalars(statement.order_by(Asset.created_at, Asset.id).limit(limit + 1))
        )
