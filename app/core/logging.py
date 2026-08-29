"""Structured JSON logging with a per-request correlation id.

Every log line carries the id of the request that produced it, which is what makes an
asynchronous flow (webhook returns 202, work continues in the background) debuggable.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


def get_request_id() -> str:
    """Correlation id of the request currently being handled ("-" outside a request)."""
    return _request_id.get()


def set_request_id(value: str) -> None:
    _request_id.set(value)


def truncate(text: str, limit: int = 120) -> str:
    """Shorten user content before logging it -- message bodies are personal data."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class JsonFormatter(logging.Formatter):
    """Renders records as single-line JSON, including any ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "requestId": getattr(record, "request_id", get_request_id()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "request_id":
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # ensure_ascii keeps emoji-carrying SMS bodies safe on non-UTF-8 stdout.
        return json.dumps(payload, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; make them propagate to ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns (or reuses) a request id and echoes it back in the response headers."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        set_request_id(request_id)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
