import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.v1.dependencies import SESSION_COOKIE
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import ApplicationError
from app.core.logging import configure_logging
from app.middleware.request_context import RequestContextMiddleware

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )

    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "If-Match",
            "Idempotency-Key",
            settings.csrf_header_name,
        ],
        expose_headers=["X-Request-ID", "Idempotency-Replayed"],
    )

    @application.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details or [],
                    "request_id": getattr(request.state, "request_id", "unavailable"),
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "La solicitud no es válida.",
                    "details": [
                        {"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()
                    ],
                    "request_id": getattr(request.state, "request_id", "unavailable"),
                }
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unavailable")
        logger.exception(
            "Unhandled application error",
            extra={"request_id": request_id, "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected error occurred.",
                    "details": [],
                    "request_id": request_id,
                }
            },
        )

    application.include_router(api_router, prefix="/api/v1")

    def openapi() -> dict:
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title,
                version=application.version,
                routes=application.routes,
            )
            schema.setdefault("components", {}).setdefault("securitySchemes", {})[
                "SessionCookie"
            ] = {"type": "apiKey", "in": "cookie", "name": SESSION_COOKIE}
            for path, operations in schema["paths"].items():
                if path != "/api/v1/assets" and not path.startswith("/api/v1/assets/"):
                    continue
                for method, operation in operations.items():
                    operation["security"] = [{"SessionCookie": []}]
                    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                        csrf_header = next(
                            parameter
                            for parameter in operation["parameters"]
                            if parameter["in"] == "header"
                            and parameter["name"].lower() == settings.csrf_header_name.lower()
                        )
                        csrf_header["required"] = True
                        csrf_header["schema"] = {
                            "type": "string",
                            "title": csrf_header["schema"]["title"],
                        }
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = openapi
    return application


app = create_app()
