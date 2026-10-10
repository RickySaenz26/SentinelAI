"""Closed human-judgement catalog; no free text or client authority fields."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.evidence.contracts import ClosedModel, bounded_json, canonical_json

Result = Literal["confirmed", "not_confirmed", "not_assessable"]


class Checklist(ClosedModel):
    console_identity: Result
    inventory_match: Result
    administrative_control: Result


class Submission(ClosedModel):
    evidence_id: UUID
    evidence_version: int = Field(ge=1, le=2**31 - 1)
    renews_review_id: UUID | None = None


class Decision(ClosedModel):
    decision: Literal["approved", "rejected"]
    checklist_version: Literal[1]
    checklist: Checklist
    reason_code: Literal["control_confirmed", "mismatch", "incomplete", "not_assessable"]

    @field_validator("checklist_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        # Literal[1] alone accepts True and 1.0 through Python equality.
        if type(value) is not int:
            raise ValueError("Checklist version must be an integer.")
        return value


class Withdrawal(ClosedModel):
    reason_code: Literal["presenter_withdrawal"]


class Revocation(ClosedModel):
    reason_code: Literal["confidence_withdrawn", "error_found"]


def decode(raw, model):
    return model.model_validate_json(canonical_json(bounded_json(raw, 16384)))


class ReviewEvent(BaseModel):
    id: UUID
    kind: str
    actor_id: UUID | None
    occurred_at: datetime
    asset_version: int
    reason_code: str
    checklist_version: int | None
    checklist: Checklist | None
    read_audit_id: UUID | None
    replacement_id: UUID | None


class Review(BaseModel):
    id: UUID
    asset_id: UUID
    evidence_id: UUID
    evidence_version: int
    presenter_id: UUID
    author_id: UUID
    asset_version: int
    snapshot: dict
    generation: int
    renews_review_id: UUID | None
    created_at: datetime
    retention_until: datetime
    version: int
    state: Literal["pending", "approved", "rejected", "withdrawn", "invalidated"]
    decided_at: datetime | None
    valid_until: datetime | None
    revoked_at: datetime | None
    invalidated_at: datetime | None
    superseded_by: UUID | None
    events: list[ReviewEvent]
    ownership_status: Literal["unverified"] = "unverified"
    request_id: str


class ReviewPage(BaseModel):
    items: list[Review]
    next_id: UUID | None
    request_id: str


class ControlSummary(BaseModel):
    asset_id: UUID
    review_count: int
    ownership_status: Literal["unverified"] = "unverified"


class ControlStatus(BaseModel):
    asset_id: UUID
    review_id: UUID | None
    valid: bool
    checked_at: datetime
    reasons: list[
        Literal[
            "no_approval",
            "revoked",
            "superseded",
            "invalidated",
            "expired",
            "asset_incompatible",
            "admission_changed",
            "evidence_unavailable",
        ]
    ]
    ownership_status: Literal["unverified"] = "unverified"
