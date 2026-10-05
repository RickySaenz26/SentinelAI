"""Closed lab attestation schema and deterministic UTF-8 representation."""

import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.evidence.errors import InvalidEvidence

MAX_DOCUMENT_BYTES = 16 * 1024


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def bounded_json(raw: bytes, limit: int) -> object:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        raise InvalidEvidence("Invalid document size or encoding.")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        raise InvalidEvidence("Invalid JSON document.") from None


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Observations(ClosedModel):
    console_identified: Literal["observed"]
    inventory_ipv4_matches: Literal["observed"]
    administrative_control: Literal["observed"]


class LabAttestation(ClosedModel):
    schema_version: int = Field(ge=1, le=1)
    method: Literal["supervised_local_console"]
    observed_at: AwareDatetime
    lab_asset_reference: str = Field(pattern=r"^LAB-[0-9]{1,6}$")
    observations: Observations
    declaration: Literal["technical_control_only_not_ownership_or_scan_permission"]

    @field_validator("observed_at")
    @classmethod
    def utc_observation(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class EvidenceContext(ClosedModel):
    """Server-supplied identities, never an alternative client target/IP."""

    organization_id: UUID
    asset_id: UUID
    dossier_id: UUID
    version: int = Field(ge=1, le=2**31 - 1)


def decode_document(raw: bytes) -> LabAttestation:
    # JSON-mode validates UUID/date strings without permitting Python coercions.
    value = bounded_json(raw, MAX_DOCUMENT_BYTES)
    try:
        return LabAttestation.model_validate_json(canonical_json(value))
    except (ValueError, ValidationError):
        raise InvalidEvidence("Invalid lab control attestation.") from None


def document_bytes(document: LabAttestation) -> bytes:
    # Revalidate even objects created using model_construct/model_copy.
    raw = canonical_json(document.model_dump(mode="json"))
    validated = decode_document(raw)
    return canonical_json(validated.model_dump(mode="json"))


def parse_submission(raw: bytes, *, now: datetime) -> LabAttestation:
    document = decode_document(raw)
    if now.tzinfo is None or not now - timedelta(hours=24) <= document.observed_at <= now:
        raise InvalidEvidence("Observation must be within the preceding 24 hours.")
    return document
