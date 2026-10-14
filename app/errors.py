"""Application errors and how they map to HTTP responses.

Services raise these exceptions; the handlers registered in `main.py` turn
them into one consistent JSON shape: `{"code", "detail", "request_id"}`.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for every error we raise on purpose."""

    status_code = 500
    code = "internal_error"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFoundError(AppError):
    """The requested node (model, organization...) does not exist."""

    status_code = 404
    code = "not_found"


class BadRequestError(AppError):
    """The request is valid JSON but asks for something we refuse (e.g. depth too big)."""

    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    """Missing or wrong API key on a protected endpoint."""

    status_code = 401
    code = "unauthorized"


class ServiceUnavailableError(AppError):
    """A dependency (Neo4j, the LLM) is down or timed out."""

    status_code = 503
    code = "service_unavailable"


def _error_body(request: Request, code: str, detail) -> dict:
    return {"code": code, "detail": detail, "request_id": getattr(request.state, "request_id", None)}


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_error_body(request, exc.code, exc.detail))


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Keep Pydantic's detailed list so the client knows which field is wrong.
    return JSONResponse(status_code=422, content=_error_body(request, "validation_error", exc.errors()))


def register_error_handlers(app: FastAPI) -> None:
    """Attach our exception handlers to the FastAPI app."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
