# Observability

## Request-ID correlation in logs

Every HTTP response carries `X-Request-ID`. Logs emitted while a request
is in flight include the same ID as a `[request=...]` segment, so a
single grep ties every log line from one request together.

### Format

Two shapes, chosen per log record:

    # No request in flight (startup, background, lifespan):
    2026-11-07 12:00:00 INFO ecofac.main:main.py:44 some startup message

    # Request in flight:
    2026-11-07 12:00:01 ERROR ecofac.api.routes.imports:imports.py:126 [request=abc-123] Post-import analysis failed

The gate is `record.request_id == "-"`. Startup logs are byte-identical
to the pre-P2-8 format, so existing log-parsing pipelines that key on
the current shape are unaffected.

### Grep recipes

    # Everything from one request
    grep 'request=abc-123' app.log

    # Every import that failed analysis
    grep 'Post-import analysis failed' app.log

    # Errors from a specific batch (combine with the batch_id in the
    # request payload visible in the surrounding lines)
    grep -B1 'Post-import analysis failed' app.log | grep -A1 batch_id=

### How it works

1. `RequestIDMiddleware` sets a `ContextVar` (`request_id_var`) at the
   start of each request and resets it in `finally`.
2. `_RequestContextFilter` (attached to every handler) copies the
   ContextVar onto `record.request_id`.
3. `_ContextFormatter` dispatches on that attribute:
   base format when `"-"`, gated format otherwise.

Because `ContextVar` state is per-asyncio-task and Starlette runs each
request in its own task, concurrent requests never see each other's IDs.

## Deployment

### uvicorn (default, no `--log-config`)

Works out of the box. `configure_logging()` (called at import of
`ecofac.main`) sees an empty root logger, installs the handler and
formatter. Uvicorn's own access/error logs are unaffected (they use
`uvicorn.access` / `uvicorn.error` loggers with their own handlers).

### gunicorn with `--log-config`, or any pre-configured root logger

`configure_logging()` detects existing handlers and does not replace
them. To get request IDs in output, the deployment's formatter must
include `%(request_id)s`:

    format: "%(asctime)s %(levelname)s %(name)s %(message)s [request=%(request_id)s]"

Without it, `record.request_id` is still set (harmless) but not
rendered. Startup logs continue to be unaffected regardless.

## What this does NOT do

- It does not switch to JSON logs. If you need that, replace the
  formatter in `configure_logging()` with a `python-json-logger` handler;
  the filter and middleware are format-agnostic.
- It does not correlate on `batch_id` or `decision_id`. Those would need
  additional `ContextVar`s and `try/finally` resets in each import/decision
  path. Deferred; `request_id` covers the common case.
- It does not propagate across worker processes. `ContextVar` is
  per-process; that is correct — the ID is unique per request, so cross-
  worker correlation still works.
