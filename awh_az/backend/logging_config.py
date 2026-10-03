"""Logging setup + request logging middleware for the AWH backend.

Before this existed nothing configured logging, so Python dropped every
logger.info() call (only WARNING+ reached stderr) and the only startup lines
visible in Render were the print() calls.

What you get:
  * One handler on the root logger writing to stdout (Render captures stdout).
  * JSON lines by default on Render / when LOG_FORMAT=json, readable text locally.
  * LOG_LEVEL env var (default INFO).
  * A request id on every log line emitted while handling a request
    (contextvar), echoed to clients in the X-Request-ID header.
  * One access line per request: method, path, station, status, duration_ms.

Never logged: headers, query strings, request/response bodies. The admin key
travels in a header, and Render logs get copied into support threads.
"""
import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# Extra fields that may be attached to a log record via logger.x(..., extra={...}).
EXTRA_FIELDS = ("method", "path", "station", "status", "duration_ms", "slow")

_HANDLER_MARK = "_awh_handler"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SLOW_REQUEST_SECONDS = 5.0
QUIET_PATHS = {"/health"}  # polled every 10 min by the Monitor workflow; DEBUG only


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for k in EXTRA_FIELDS:
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def _use_json() -> bool:
    fmt = os.environ.get("LOG_FORMAT", "").lower()
    if fmt:
        return fmt == "json"
    return bool(os.environ.get("RENDER"))  # Render sets RENDER=true


def setup_logging() -> None:
    """Configure root logging. Safe to call more than once."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(_RequestIdFilter())
    if _use_json():
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    # Route uvicorn's own messages through our handler (one format), and drop its
    # access log because RequestLoggingMiddleware logs a richer line per request.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

    for noisy in ("google", "grpc", "urllib3", "firebase_admin", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RequestLoggingMiddleware:
    """Pure ASGI middleware: request id, access log line, 500 handling."""

    def __init__(self, app):
        self.app = app
        self.log = logging.getLogger("awh.request")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        state = {"status": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception:
                f = self._fields(scope, 500, start)
                self.log.exception("unhandled exception on %s %s", f["method"], f["path"], extra=f)
                if state["status"] is None:  # nothing sent yet: answer with a traceable 500
                    state["status"] = 500
                    body = json.dumps({"detail": "Internal Server Error", "request_id": rid}).encode()
                    await send_wrapper({"type": "http.response.start", "status": 500, "headers": [
                        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                    await send_wrapper({"type": "http.response.body", "body": body})
                else:
                    raise
                return

            status = state["status"] or 0
            fields = self._fields(scope, status, start)
            if scope["path"] in QUIET_PATHS and status < 400:
                level = logging.DEBUG
            elif status >= 500:
                level = logging.ERROR
            elif status >= 400 or fields["slow"]:
                level = logging.WARNING
            else:
                level = logging.INFO
            self.log.log(level, "%s %s -> %s (%.1f ms)", fields["method"], fields["path"],
                         fields["status"], fields["duration_ms"], extra=fields)
        finally:
            request_id_var.reset(token)

    @staticmethod
    def _fields(scope, status, start) -> dict:
        duration = time.perf_counter() - start
        fields = {
            "method": scope["method"],
            "path": scope["path"],
            "status": status,
            "duration_ms": round(duration * 1000, 1),
            "slow": duration > SLOW_REQUEST_SECONDS,
        }
        station = (scope.get("path_params") or {}).get("station_name")
        if station:
            fields["station"] = station
        return fields
