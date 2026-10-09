"""Single persisted authority. Call inside the authenticated organization's lock."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from app.assets.policy import LabPolicy
from app.core.errors import ApplicationError


def denied():
    raise ApplicationError("LAB_POLICY_DENIED", "Objetivo no admitido por política publicada.", 403)


@dataclass(frozen=True)
class PublishedPolicy:
    revision_id: UUID
    sequence: int
    policy: LabPolicy


def current_policy(session, organization_id) -> PublishedPolicy | None:
    row = (
        session.execute(
            text(
                "SELECT r.* FROM lab_policy_current c JOIN lab_policy_revisions r "
                "ON (r.organization_id,r.id)=(c.organization_id,c.revision_id) "
                "WHERE c.organization_id=:org"
            ),
            {"org": organization_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    policy = LabPolicy.model_validate_json(row["snapshot"])
    if policy.fingerprint != row["policy_hash"]:
        denied()
    return PublishedPolicy(row["id"], row["sequence"], policy)


def require_target(session, organization_id, target) -> PublishedPolicy:
    published = current_policy(session, organization_id)
    if published is None or not published.policy.permits(target):
        denied()
    return published


def require_admission(session, organization_id, asset_id, *, generation=None):
    row = (
        session.execute(
            text(
                "SELECT h.*,s.canonical_target,s.deleted_at FROM asset_admission_current c "
                "JOIN asset_admission_history h ON "
                "(h.organization_id,h.asset_id,h.generation)="
                "(c.organization_id,c.asset_id,c.generation) "
                "JOIN assets s ON (s.organization_id,s.id)=(c.organization_id,c.asset_id) "
                "WHERE c.organization_id=:org AND c.asset_id=:asset"
            ),
            {"org": organization_id, "asset": asset_id},
        )
        .mappings()
        .first()
    )
    if row is None or not row["admitted"] or row["deleted_at"] is not None:
        denied()
    published = require_target(session, organization_id, row["canonical_target"])
    if generation is not None and row["generation"] != generation:
        raise ApplicationError("ADMISSION_CHANGED", "La generación de admisión cambió.", 409)
    return published, row["generation"]
