"""Thin HTTP adapter for laboratory assets, not scan authorization."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_actor
from app.assets.schemas import (
    AssetArchive,
    AssetCreate,
    AssetErrorResponse,
    AssetListResponse,
    AssetPatch,
    AssetResponse,
    Criticality,
)
from app.assets.service import AssetService
from app.authorization.policy import ActorContext
from app.platform.database.session import get_db_session

router = APIRouter(
    prefix="/assets",
    responses={
        code: {"model": AssetErrorResponse, "description": description}
        for code, description in {
            401: "Session missing, expired or revoked.",
            403: "Permission, CSRF, trusted origin or laboratory policy denied.",
            404: "Asset not available in the authenticated tenant.",
            409: "Duplicate, quota, version, archive or idempotency conflict.",
            422: "Invalid input, header or cursor.",
            429: "Process-local rate limit exceeded; see Retry-After.",
        }.items()
    },
)
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]*$",
    ),
]
Version = Annotated[int, Header(alias="If-Match", ge=1)]


def get_service(
    request: Request,
    actor: ActorContext = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> AssetService:
    return AssetService(session, actor, request.state.request_id)


@router.post("", status_code=201, response_model=AssetResponse)
def create_asset(
    payload: AssetCreate,
    response: Response,
    idempotency_key: IdempotencyKey,
    service: AssetService = Depends(get_service),
):
    """Register an unverified lab asset; never contact it or authorize scanning.

    Idempotency-Key is scoped to actor/tenant/method/route for 24 hours. Replay
    returns the original response (not current state); use GET to refresh it.
    Current session, permission, CSRF and policy are checked before replay.
    """
    body, replayed = service.create(payload, idempotency_key)
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return body


@router.get("", response_model=AssetListResponse)
def list_assets(
    service: AssetService = Depends(get_service),
    limit: int = Query(default=50, alias="page[limit]", ge=1, le=100),
    cursor: str | None = Query(default=None, alias="page[after]", max_length=512),
    status: Literal["active", "archived", "all"] = "active",
    criticality: Criticality | None = None,
    q: str | None = Query(default=None, max_length=100),
    type: Literal["ipv4"] | None = None,
):
    return service.list(limit=limit, cursor=cursor, status=status, criticality=criticality, query=q)


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: UUID, service: AssetService = Depends(get_service)):
    return service.get(asset_id)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: UUID,
    payload: AssetPatch,
    if_match: Version,
    service: AssetService = Depends(get_service),
):
    """Change metadata only. If-Match is the positive integer asset version.

    This preserves the existing API convention (409 on stale version, not 412).
    """
    return service.update(asset_id, payload, if_match)


@router.delete("/{asset_id}", status_code=204)
def archive_asset(
    asset_id: UUID,
    payload: AssetArchive,
    if_match: Version,
    idempotency_key: IdempotencyKey,
    service: AssetService = Depends(get_service),
):
    """Archive with reason and version; retry the same key/body/version for replay.

    No physical deletion and no restoration. Still permitted when operator policy
    removes the target. Both POST and DELETE expose Idempotency-Replayed.
    """
    replayed = service.archive(asset_id, payload, if_match, idempotency_key)
    return Response(status_code=204, headers={"Idempotency-Replayed": str(replayed).lower()})
