"""Public evidence metadata; cryptographic receipts never cross this boundary."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EvidenceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    asset_id: UUID
    version: int
    submitted_at: datetime
    retention_until: datetime
    content_available: bool
    request_id: str


class EvidencePage(BaseModel):
    items: list[EvidenceMetadata]
    next_version: int | None
    request_id: str


class EvidenceSummary(BaseModel):
    asset_id: UUID
    evidence_count: int
    ownership_status: Literal["unverified"]
    request_id: str
