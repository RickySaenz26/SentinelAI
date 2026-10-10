"""API-first control review: bounded JSON, fresh authority, no positive cache."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
from starlette.concurrency import run_in_threadpool

from app.api.v1.assets import IdempotencyKey, Version
from app.api.v1.evidence import NO_STORE, bounded_body, get_access
from app.assets.schemas import AssetErrorResponse
from app.control_reviews.schemas import (
    ControlStatus,
    ControlSummary,
    Decision,
    Review,
    ReviewPage,
    Revocation,
    Submission,
    Withdrawal,
    decode,
)
from app.control_reviews.service import ControlReviews
from app.core.errors import ApplicationError
from app.evidence.access import EvidenceAccess
from app.evidence.errors import InvalidEvidence


class ControlRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
            except ApplicationError as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise
            except (ValidationError, RequestValidationError, InvalidEvidence):
                raise ApplicationError(
                    "INVALID_CONTROL_REQUEST",
                    "Solicitud no válida.",
                    422,
                    headers={"Cache-Control": "no-store"},
                ) from None
            except DBAPIError as error:
                state = getattr(error.orig, "sqlstate", None)
                constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                codes = {
                    "control_asset_version": "VERSION_CONFLICT",
                    "control_review_version": "VERSION_CONFLICT",
                    "control_evidence_owner": "CONTROL_EVIDENCE_REFERENCE",
                    "control_separation": "CONTROL_SEPARATION_REQUIRED",
                    "control_reading": "CONTROL_READING_REQUIRED",
                    "uq_control_pending": "CONTROL_PENDING_EXISTS",
                    "control_checklist": "CONTROL_CHECKLIST_INVALID",
                    "control_judgement": "CONTROL_JUDGEMENT_INVALID",
                }
                status = (
                    403
                    if state == "42501"
                    else 409
                    if state in {"23505", "23514", "23503"}
                    else 503
                )
                code = "FORBIDDEN" if status == 403 else codes.get(constraint, "CONTROL_CONFLICT")
                if status == 503:
                    code = "CONTROL_UNAVAILABLE"
                raise ApplicationError(
                    code, "Revisión no disponible.", status, headers={"Cache-Control": "no-store"}
                ) from None
            except Exception:
                raise ApplicationError(
                    "CONTROL_UNAVAILABLE",
                    "Revisión no disponible.",
                    503,
                    headers={"Cache-Control": "no-store"},
                ) from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


router = APIRouter(
    route_class=ControlRoute,
    responses={
        code: {"model": AssetErrorResponse, "headers": NO_STORE, "description": description}
        for code, description in {
            401: "Session revoked, expired or changed.",
            403: "Role, permission, CSRF or Origin denied.",
            404: "Resource unavailable in authorized tenant/ownership scope.",
            409: "Version, lifecycle, reading, checklist or idempotency conflict.",
            410: "Evidence retention elapsed.",
            413: "Streaming body exceeds 16384 bytes.",
            415: "Only uncompressed application/json, optional charset=utf-8.",
            422: "Strict request validation failed.",
            503: "Storage/database unavailable; commit may be uncertain, retry the same key.",
        }.items()
    },
)


def get_reviews(access: Annotated[EvidenceAccess, Depends(get_access)]):
    return ControlReviews(access)


Service = Annotated[ControlReviews, Depends(get_reviews)]
REPLAY_RESPONSE = {
    201: {
        "headers": {
            **NO_STORE,
            "Idempotency-Replayed": {
                "description": "Historical pointer replay, never a statement of current validity.",
                "schema": {"type": "string", "enum": ["true", "false"]},
            },
        }
    }
}


def body_schema(model):
    schema = model.model_json_schema()
    # Inline local definitions to keep the manual bounded-body OpenAPI self-contained.
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].split("/")[-1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return {
        "parameters": [
            {
                "name": "Origin",
                "in": "header",
                "required": False,
                "description": "If present must be trusted, otherwise 403.",
                "schema": {"type": "string"},
            }
        ],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": inline(schema), "x-max-bytes": 16384}},
        },
    }


async def mutation(
    request, response, service, action, model, version, key, *, asset_id=None, review_id=None
):
    # Authenticate before consuming a potentially streamed body.
    def preflight():
        with service.transaction() as db:
            service.authorize(db, action)

    await run_in_threadpool(preflight)
    payload = decode(await bounded_body(request), model)
    document, replayed = await run_in_threadpool(
        service.mutate, asset_id, review_id, action, payload, version, key, request.url.path
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    return document


@router.post(
    "/assets/{asset_id}/control-reviews",
    status_code=201,
    response_model=Review,
    responses=REPLAY_RESPONSE,
    openapi_extra=body_schema(Submission),
)
async def submit(
    asset_id: UUID,
    request: Request,
    response: Response,
    if_match: Version,
    idempotency_key: IdempotencyKey,
    service: Service,
):
    """Own confirmed evidence only. If-Match is the asset version. Replay lasts 24h."""
    return await mutation(
        request,
        response,
        service,
        "submit",
        Submission,
        if_match,
        idempotency_key,
        asset_id=asset_id,
    )


@router.get(
    "/assets/{asset_id}/control-reviews",
    response_model=ReviewPage,
    responses={200: {"headers": NO_STORE}},
)
def listing(
    asset_id: UUID,
    service: Service,
    limit: int = Query(50, ge=1, le=100),
    after: UUID | None = None,
):
    return service.listing(asset_id, limit, after)


@router.get(
    "/control-reviews/{review_id}", response_model=Review, responses={200: {"headers": NO_STORE}}
)
def get_review(review_id: UUID, service: Service):
    """Historical request/decision, not current effective validity."""
    return service.get(review_id)


@router.post(
    "/control-reviews/{review_id}/decisions",
    status_code=201,
    response_model=Review,
    responses=REPLAY_RESPONSE,
    openapi_extra=body_schema(Decision),
)
async def decide(
    review_id: UUID,
    request: Request,
    response: Response,
    if_match: Version,
    idempotency_key: IdempotencyKey,
    service: Service,
):
    """If-Match binds review version; prior audited reading by this other user is mandatory."""
    return await mutation(
        request,
        response,
        service,
        "decide",
        Decision,
        if_match,
        idempotency_key,
        review_id=review_id,
    )


@router.post(
    "/control-reviews/{review_id}/withdrawals",
    status_code=201,
    response_model=Review,
    responses=REPLAY_RESPONSE,
    openapi_extra=body_schema(Withdrawal),
)
async def withdraw(
    review_id: UUID,
    request: Request,
    response: Response,
    if_match: Version,
    idempotency_key: IdempotencyKey,
    service: Service,
):
    """Only presenter withdraws a pending review; If-Match binds review version."""
    return await mutation(
        request,
        response,
        service,
        "withdraw",
        Withdrawal,
        if_match,
        idempotency_key,
        review_id=review_id,
    )


@router.post(
    "/control-reviews/{review_id}/revocations",
    status_code=201,
    response_model=Review,
    responses=REPLAY_RESPONSE,
    openapi_extra=body_schema(Revocation),
)
async def revoke(
    review_id: UUID,
    request: Request,
    response: Response,
    if_match: Version,
    idempotency_key: IdempotencyKey,
    service: Service,
):
    """Owner/manager may revoke even their own approval; If-Match binds review version."""
    return await mutation(
        request,
        response,
        service,
        "revoke",
        Revocation,
        if_match,
        idempotency_key,
        review_id=review_id,
    )


@router.get(
    "/assets/{asset_id}/control-status",
    response_model=ControlStatus,
    responses={200: {"headers": NO_STORE}},
)
def status(asset_id: UUID, service: Service):
    """Fresh decryption/integrity + reauthorization; checked_at and closed failure reasons."""
    return service.status(asset_id)


@router.get(
    "/assets/{asset_id}/control-summary",
    response_model=ControlSummary,
    responses={200: {"headers": NO_STORE}},
)
def summary(asset_id: UUID, service: Service):
    """Count only. No identities, reasons, evidence or statement of validity."""
    return service.summary(asset_id)
