"""Application error hierarchy and the FastAPI handlers that render it.

Every expected failure is raised as an :class:`AppError`, so the API layer can turn it
into one consistent JSON shape instead of leaking provider-specific exceptions.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that map to a known HTTP response."""

    code: str = "internal_error"
    status_code: int = 500
    default_message: str = "Internal server error"

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class ConfigurationError(AppError):
    code = "configuration_error"
    status_code = 500
    default_message = "The service is misconfigured"


class StorageError(AppError):
    code = "storage_error"
    status_code = 500
    default_message = "Storage is unavailable"


class LLMError(AppError):
    code = "llm_error"
    status_code = 502
    default_message = "The LLM provider failed to generate a response"


class SmsProviderError(AppError):
    code = "sms_provider_error"
    status_code = 502
    default_message = "The SMS provider failed to deliver the message"


class InvalidWebhookError(AppError):
    code = "invalid_webhook"
    status_code = 400
    default_message = "The webhook payload could not be parsed"


class InvalidPhoneNumberError(InvalidWebhookError):
    code = "invalid_phone_number"
    default_message = "The phone number is not a valid E.164 number"


class SignatureVerificationError(AppError):
    code = "invalid_signature"
    status_code = 403
    default_message = "The webhook signature could not be verified"


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    default_message = "Resource not found"


def error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Single response shape for every error the API returns."""
    body: dict[str, Any] = {"code": code, "message": message, "requestId": get_request_id()}
    if extra:
        body.update(extra)
    return {"error": body}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so no unexpected exception escapes as an HTML traceback."""

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        log = logger.error if exc.status_code >= 500 else logger.warning
        log("%s: %s", exc.code, exc.message, extra={"details": exc.details})
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, **exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "validation_error",
                "The request payload is invalid",
                fields=[
                    {"loc": list(err.get("loc", [])), "msg": err.get("msg", "")}
                    for err in exc.errors()
                ],
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload("http_error", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_payload("internal_error", "Internal server error"),
        )
