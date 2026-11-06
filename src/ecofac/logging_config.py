# Copyright 2026 Lightibulb
# Request-scoped logging context.
#
# RequestIDMiddleware sets request_id_var around each request. A logging
# Filter attached to each handler pushes the value onto the LogRecord as
# record.request_id. A formatter then renders it -- but only when the
# record is inside a request, so startup logs keep the current look.
#
# Gated format (per record):
#   request in flight:  ... [request=abc123] message
#   no request:         ... message

import contextvars
import logging

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ecofac_request_id", default=None
)

BASE_FMT = "%(asctime)s %(levelname)s %(name)s:%(filename)s:%(lineno)d %(message)s"
CTX_FMT = (
    "%(asctime)s %(levelname)s %(name)s:%(filename)s:%(lineno)d "
    "[request=%(request_id)s] %(message)s"
)


class _RequestContextFilter(logging.Filter):
    # Attach record.request_id from the ContextVar. Any value passed via
    # extra={"request_id": ...} is preserved (hasattr check).

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get() or "-"
        return True


class _ContextFormatter(logging.Formatter):
    # Two sub-formatters; dispatch per record on record.request_id.

    def __init__(self, base_format: str, context_format: str) -> None:
        super().__init__(base_format)
        self._base_formatter = logging.Formatter(base_format)
        self._context_formatter = logging.Formatter(context_format)

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", "-")
        if request_id and request_id != "-":
            return self._context_formatter.format(record)
        return self._base_formatter.format(record)


_configured = False


def _has_our_filter(handler: logging.Handler) -> bool:
    return any(isinstance(f, _RequestContextFilter) for f in handler.filters)


def configure_logging(level: int = logging.INFO) -> None:
    # Install the filter + formatter once per process.
    #
    # Two cases:
    #
    # 1. Root has no handlers (uvicorn's default; also the case when
    #    this runs under plain `python -m ecofac`). Install our own
    #    handler + formatter. Request-scoped logs get [request=ID],
    #    startup logs keep the current look.
    #
    # 2. Root already has handlers (gunicorn with --log-config,
    #    uvicorn --log-config, a deployment's own setup). Attach the
    #    filter so record.request_id exists, but DO NOT replace the
    #    deployment's formatter. If the deployment wants request IDs in
    #    its output, include %(request_id)s in its format.
    global _configured  # noqa: PLW0603
    if _configured:
        return

    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            if not _has_our_filter(handler):
                handler.addFilter(_RequestContextFilter())
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(_ContextFormatter(BASE_FMT, CTX_FMT))
        handler.addFilter(_RequestContextFilter())
        root.addHandler(handler)
        if root.level > level:
            root.setLevel(level)

    _configured = True
