"""Bounded evidence HTTP boundary; no uploads, target connectivity or approval."""

import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from app.api.v1.assets import IdempotencyKey, Version
from app.api.v1.dependencies import SESSION_COOKIE
from app.assets.schemas import AssetErrorResponse
from app.core.config import get_settings
from app.core.errors import ApplicationError
from app.evidence.access import EvidenceAccess
from app.evidence.configuration import configured_storage
from app.evidence.contracts import MAX_DOCUMENT_BYTES, LabAttestation
from app.evidence.coordination import EvidenceBusy
from app.evidence.errors import InvalidEvidence
from app.evidence.layout import protected_root
from app.evidence.schemas import EvidenceMetadata, EvidencePage, EvidenceSummary
from app.evidence.service import SessionCredentials, fail
from app.platform.database.session import get_session_factory


class SensitiveRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
            except ApplicationError as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            except (InvalidEvidence, RequestValidationError):
                raise ApplicationError(
                    "INVALID_EVIDENCE_REQUEST",
                    "Solicitud no válida.",
                    422,
                    headers={"Cache-Control": "no-store"},
                ) from None
            except EvidenceBusy:
                raise ApplicationError(
                    "EVIDENCE_BUSY",
                    "Reintenta más tarde.",
                    409,
                    headers={"Cache-Control": "no-store"},
                ) from None
            except Exception:
                # Never let SQL parameters, submitted data or storage paths reach
                # the global exception logger. Uncertain outcomes remain recoverable.
                raise ApplicationError(
                    "EVIDENCE_UNAVAILABLE",
                    "Evidencia no disponible.",
                    503,
                    headers={"Cache-Control": "no-store"},
                ) from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


NO_STORE = {
    "Cache-Control": {
        "description": "Sensitive responses must not be stored, including errors.",
        "schema": {"type": "string", "const": "no-store"},
    }
}
router = APIRouter(
    prefix="/evidence/assets",
    route_class=SensitiveRoute,
    responses={
        code: {"model": AssetErrorResponse, "description": description, "headers": NO_STORE}
        for code, description in {
            401: "Session or membership missing, expired, switched or revoked.",
            403: "Explicit permission, CSRF, Origin or current write policy denied.",
            404: "Resource unavailable in authorized tenant/ownership scope.",
            409: "Asset version, quota, idempotency, pending operation or coordinator conflict.",
            410: "Authorized content is beyond its 90-day retention window.",
            413: "Body exceeds 16384 bytes, including streamed/chunked requests.",
            415: "Only uncompressed application/json (optional charset=utf-8).",
            422: "Invalid document or request; duplicate/extra fields are rejected.",
            503: "Evidence, database or access audit unavailable; commit may be uncertain.",
        }.items()
    },
)


def get_access(
    request: Request,
    csrf_token: str | None = Header(default=None, alias=get_settings().csrf_header_name),
):
    credentials = SessionCredentials(
        request.cookies.get(SESSION_COOKIE, ""),
        csrf_token or "",
        method=request.method,
        origin=request.headers.get("origin"),
    )
    return EvidenceAccess(
        get_session_factory(),
        credentials,
        request.state.request_id,
        lambda: configured_storage(os.environ, repository_root=protected_root(Path(__file__))),
    )


Access = Annotated[EvidenceAccess, Depends(get_access)]
DOCUMENT_SCHEMA = LabAttestation.model_json_schema(ref_template="#/components/schemas/{model}")
DOCUMENT_SCHEMA.pop("$defs", None)


async def bounded_body(request: Request):
    for name in (
        "content-type",
        "content-length",
        "content-encoding",
        "if-match",
        "idempotency-key",
        "origin",
        get_settings().csrf_header_name,
    ):
        if len(request.headers.getlist(name)) > 1:
            fail("DUPLICATE_HEADER", 422)
    content_type = request.headers.get("content-type", "").lower().replace(" ", "")
    if content_type not in {"application/json", "application/json;charset=utf-8"} or (
        "content-encoding" in request.headers
    ):
        fail("UNSUPPORTED_MEDIA_TYPE", 415)
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            fail("INVALID_CONTENT_LENGTH", 422)
        if len(length) > 10 or int(length) > MAX_DOCUMENT_BYTES:
            fail("EVIDENCE_TOO_LARGE", 413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_DOCUMENT_BYTES:
            fail("EVIDENCE_TOO_LARGE", 413)
        body.extend(chunk)
    return bytes(body)


@router.post(
    "/{asset_id}",
    status_code=201,
    response_model=EvidenceMetadata,
    responses={
        201: {
            "headers": {
                **NO_STORE,
                "Idempotency-Replayed": {
                    "description": "true for an authorized replay; false for a new presentation.",
                    "schema": {"type": "string", "enum": ["true", "false"]},
                },
            }
        }
    },
    openapi_extra={
        "parameters": [
            {
                "name": "Origin",
                "in": "header",
                "required": False,
                "description": (
                    "When provided, must match a configured trusted origin; otherwise 403."
                ),
                "schema": {"type": "string"},
            }
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": DOCUMENT_SCHEMA, "x-max-bytes": MAX_DOCUMENT_BYTES}
            },
        },
    },
)
async def present_evidence(
    asset_id: UUID,
    request: Request,
    response: Response,
    if_match: Version,
    idempotency_key: IdempotencyKey,
    access: Access,
):
    """Present technical evidence only; never ownership or scan authorization.

    16 KiB streaming cap; no compression. Required If-Match binds the asset version.
    Replay lasts 24 hours, binds actor/tenant/method/route/document/precondition and
    returns fresh metadata after authorization. A pending operation is not repeated.
    """
    before = await run_in_threadpool(access.preflight, asset_id)
    raw = await bounded_body(request)
    body, replayed = await run_in_threadpool(
        access.submit, asset_id, if_match, idempotency_key, raw, before
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return body


@router.get(
    "/{asset_id}/summary", response_model=EvidenceSummary, responses={200: {"headers": NO_STORE}}
)
def evidence_summary(asset_id: UUID, access: Access):
    """Minimal count only, scoped to the active tenant; unverified is not authority."""
    return access.summary(asset_id)


@router.get("/{asset_id}", response_model=EvidencePage, responses={200: {"headers": NO_STORE}})
def list_evidence(
    asset_id: UUID,
    access: Access,
    limit: int = Query(default=50, ge=1, le=100),
    after_version: int = Query(default=0, ge=0, le=2**31 - 1),
):
    """Private metadata; analysts see only their own submissions. Includes archived assets."""
    return access.listing(asset_id, limit, after_version)


@router.get(
    "/{asset_id}/{evidence_id}",
    response_model=EvidenceMetadata,
    responses={200: {"headers": NO_STORE}},
)
def evidence_metadata(asset_id: UUID, evidence_id: UUID, access: Access):
    return access.get(asset_id, evidence_id)


@router.get(
    "/{asset_id}/{evidence_id}/content",
    response_model=LabAttestation,
    responses={200: {"headers": NO_STORE}},
)
def evidence_content(asset_id: UUID, evidence_id: UUID, access: Access):
    """Read only after renewed authorization and committed access audit.

    Archived assets and removed IPs remain readable. After 90 days, return 410;
    no automatic purge. Cache-Control: no-store, including sanitized failures.
    """
    return access.content(asset_id, evidence_id)
