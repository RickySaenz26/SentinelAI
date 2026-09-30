"""Strict inventory contracts; authority and target changes are never accepted."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.assets.policy import exact_ipv4

Criticality = Literal["low", "medium", "high", "critical"]


class AssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssetCreate(AssetInput):
    type: Literal["ipv4"]
    target: str = Field(min_length=7, max_length=15)
    display_name: str = Field(min_length=1, max_length=160, pattern=r"\S")
    criticality: Criticality

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        return exact_ipv4(value)


class AssetPatch(AssetInput):
    display_name: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"\S")
    criticality: Criticality | None = None

    @model_validator(mode="after")
    def nonempty_metadata(self) -> "AssetPatch":
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null metadata field.")
        return self


class AssetArchive(AssetInput):
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class AssetResponse(BaseModel):
    id: UUID
    type: Literal["ipv4"]
    target: str
    display_name: str
    criticality: Criticality
    ownership_status: Literal["unverified"]
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    archive_reason: str | None
    request_id: str


class AssetPage(BaseModel):
    next_cursor: str | None
    limit: int


class AssetListResponse(BaseModel):
    items: list[AssetResponse]
    page: AssetPage
    request_id: str


class AssetApiError(BaseModel):
    code: str
    message: str
    details: list[dict[str, object]]
    request_id: str


class AssetErrorResponse(BaseModel):
    error: AssetApiError
