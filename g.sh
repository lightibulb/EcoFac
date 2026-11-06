#!/usr/bin/env bash
# =============================================================================
# EcoFac - P2-8 test fixes
#
# Two test-only bugs from the previous run:
#
#   1. test_error_log_carries_request_id lives in tests/unit/ but needs
#      the `client` fixture (only defined in tests/api/conftest.py).
#      It's an HTTP-level test -> move to tests/api/.
#
#   2. test_filter_preserves_existing_extra sets request_id_var without
#      resetting. The next test then sees "from-var" instead of None.
#      Fixed by an autouse fixture that clears the ContextVar before
#      and after every test in both files.
#
# Production code untouched (logging_config.py, middleware.py, main.py
# all correct from prior runs). Idempotent. No ''' or """ anywhere.
# =============================================================================
set -euo pipefail

RUFF="${RUFF:-ruff}"
PYTEST="${PYTEST:-pytest}"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
abort() { printf '%sABORT:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
info()  { printf '%s==%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '%s!!%s %s\n' "$YELLOW" "$RESET" "$*"; }
step()  { printf '\n%s>>> %s%s\n' "$BLUE" "$*" "$RESET"; }

BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ecofac-p2-8-testfix-XXXXXX")"
on_exit() { printf '\nbackups: %s\n' "$BACKUP_DIR"; }
trap on_exit EXIT
backup() { [[ -f "$1" ]] && cp -a "$1" "$BACKUP_DIR/$(printf '%s' "$1" | tr '/' '_')" || true; }

step "Preflight"
[[ -f src/ecofac/logging_config.py ]] || abort "missing: logging_config.py"
[[ -f src/ecofac/api/middleware.py ]] || abort "missing: middleware.py"
[[ -f tests/unit/test_logging_context.py ]] || abort "missing: test_logging_context.py"
grep -q "configure_logging" src/ecofac/main.py || abort "main.py wiring missing"
info "P2-8 production wiring confirmed"

# -----------------------------------------------------------------------------
step "Fix 1: rewrite tests/unit/test_logging_context.py (autouse reset, no client)"
backup tests/unit/test_logging_context.py

cat > tests/unit/test_logging_context.py <<'PYEOF'
# P2-8: request-ID logging context tests (unit level).
#
# The integration test (real HTTP request) lives in
# tests/api/test_logging_context_integration.py, because it needs the
# `client` fixture which is provided by tests/api/conftest.py.
#
# Autouse fixture clears request_id_var before and after every test.
# Without it, test_filter_preserves_existing_extra's `set("from-var")`
# leaks into the next test, and the middleware's try/finally reset
# restores to that leaked value instead of None.

import asyncio
import logging

import pytest
from ecofac.api.middleware import RequestIDMiddleware
from ecofac.logging_config import (
    BASE_FMT,
    CTX_FMT,
    _ContextFormatter,
    _RequestContextFilter,
    request_id_var,
)


@pytest.fixture(autouse=True)
def _clean_request_id():
    request_id_var.set(None)
    yield
    request_id_var.set(None)


def _record(level=logging.INFO, msg="hello"):
    return logging.LogRecord(
        name="ecofac.test",
        level=level,
        pathname="/x.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_filter_uses_dash_when_no_context():
    rec = _record()
    _RequestContextFilter().filter(rec)
    assert rec.request_id == "-"


def test_filter_uses_contextvar_when_set():
    token = request_id_var.set("req-42")
    try:
        rec = _record()
        _RequestContextFilter().filter(rec)
        assert rec.request_id == "req-42"
    finally:
        request_id_var.reset(token)


def test_filter_preserves_existing_extra():
    request_id_var.set("from-var")
    rec = _record()
    rec.request_id = "from-extra"
    _RequestContextFilter().filter(rec)
    assert rec.request_id == "from-extra"


def test_formatter_base_when_no_request_id():
    fmt = _ContextFormatter(BASE_FMT, CTX_FMT)
    rec = _record(msg="startup")
    rec.request_id = "-"
    out = fmt.format(rec)
    assert "[request=" not in out
    assert "startup" in out


def test_formatter_context_when_request_id_present():
    fmt = _ContextFormatter(BASE_FMT, CTX_FMT)
    rec = _record(msg="in-request")
    rec.request_id = "abc-123"
    out = fmt.format(rec)
    assert "[request=abc-123]" in out
    assert "in-request" in out


def test_middleware_sets_and_resets_contextvar():
    seen = []

    async def wrapped_app(_scope, _receive, send):
        seen.append(request_id_var.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run():
        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(_msg):
            return None

        middleware = RequestIDMiddleware(wrapped_app)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/x",
            "raw_path": b"/x",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"x-request-id", b"test-abc")],
            "client": ("testclient", 12345),
            "server": ("testserver", 80),
        }
        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen == ["test-abc"]
    # Autouse fixture guarantees None before this test; the middleware's
    # try/finally must restore exactly that value.
    assert request_id_var.get() is None
PYEOF
info "rewrote tests/unit/test_logging_context.py"

# -----------------------------------------------------------------------------
step "Fix 2: create tests/api/test_logging_context_integration.py"
backup tests/api/test_logging_context_integration.py

cat > tests/api/test_logging_context_integration.py <<'PYEOF'
# P2-8: request-ID logging context (integration).
#
# Uses the `client` fixture (tests/api/conftest.py) to hit the real app.
# Asserts that an ERROR log emitted while a request is in flight carries
# the same request_id that the client sent (or that middleware generated).
#
# Separated from tests/unit/test_logging_context.py because it needs the
# ASGI client and the DB. The unit tests stay fixture-free.

import logging
import sqlite3

import pytest
from ecofac.api.routes import imports as imports_route
from ecofac.logging_config import _RequestContextFilter, request_id_var


@pytest.fixture(autouse=True)
def _clean_request_id():
    request_id_var.set(None)
    yield
    request_id_var.set(None)


def _spike_csv():
    lines = ["timestamp,building_id,meter_id,energy_value,unit"]
    lines.extend(
        f"2026-01-01T{h:02d}:00:00+00:00,b1,m1,100.0,kwh" for h in range(24)
    )
    lines.append("2026-01-02T00:00:00+00:00,b1,m1,5000.0,kwh")
    return "\n".join(lines) + "\n"


def test_error_log_carries_request_id(client, caplog, monkeypatch):
    # Attach the filter so caplog records get request_id, then detach
    # in finally. caplog.handler is session-scoped; leaking the filter
    # into other tests would be a side effect (harmless, but avoid it).
    filter_obj = _RequestContextFilter()
    caplog.handler.addFilter(filter_obj)

    def boom(*_args, **_kwargs):
        raise sqlite3.Error("simulated")

    monkeypatch.setattr(imports_route, "analyze_all", boom)

    try:
        with caplog.at_level(logging.ERROR, logger="ecofac"):
            r = client.post(
                "/api/v1/imports",
                files={"file": ("data.csv", _spike_csv().encode(), "text/csv")},
                data={"source_id": "sid-p28", "batch_id": "bid-p28"},
                headers={"X-Request-ID": "req-test-p28"},
            )

        assert r.status_code == 200, r.text
        assert r.headers["X-Request-ID"] == "req-test-p28"

        matching = [
            rec
            for rec in caplog.records
            if "Post-import analysis failed" in rec.getMessage()
        ]
        assert matching, "expected the analysis-failure ERROR log"
        assert matching[0].request_id == "req-test-p28"
    finally:
        caplog.handler.removeFilter(filter_obj)
PYEOF
info "created tests/api/test_logging_context_integration.py"

# -----------------------------------------------------------------------------
step "Auto-format"
$RUFF format \
    tests/unit/test_logging_context.py \
    tests/api/test_logging_context_integration.py \
  || abort "ruff format failed"

step "Lint"
$RUFF check src tests simulator evaluation scripts || abort "ruff check failed"
info "lint clean"

# -----------------------------------------------------------------------------
step "Sanity: configure_logging installed handler"
python3 - <<'PYEOF'
import importlib
import logging
import sys

for mod in ("ecofac.main", "ecofac.logging_config", "ecofac.api.middleware"):
    sys.modules.pop(mod, None)

importlib.import_module("ecofac.main")

from ecofac.logging_config import (
    _ContextFormatter,
    _RequestContextFilter,
    request_id_var,
)

root = logging.getLogger()
assert root.handlers, "configure_logging() did not install a handler"
has_fmt = any(isinstance(h.formatter, _ContextFormatter) for h in root.handlers)
has_flt = any(
    any(isinstance(f, _RequestContextFilter) for f in h.filters)
    for h in root.handlers
)
assert has_fmt, "no _ContextFormatter on root handler"
assert has_flt, "no _RequestContextFilter on root handler"
assert request_id_var.get() is None, "ContextVar should start unset"
print("OK: configure_logging installed formatter + filter; ContextVar unset")
PYEOF

# -----------------------------------------------------------------------------
step "Focused tests"
FOCUS=(
  tests/unit/test_logging_context.py
  tests/api/test_logging_context_integration.py
  tests/api/test_middleware.py
  tests/api/test_api_smoke.py
  tests/api/test_import_api_reliability.py
  tests/api/test_import_analysis_retry.py
  tests/api/test_audit_regressions.py
  tests/api/test_rate_limit_api.py
  tests/unit/test_rate_limit.py
  tests/integration/test_import_reliability.py
  tests/integration/test_ingestion.py
)
$PYTEST --tb=short -q "${FOCUS[@]}" || abort "focused tests failed"

step "Summary"
cat <<'OUT'
P2-8 complete:

  tests/unit/test_logging_context.py             6 tests, no fixtures
    - autouse fixture clears request_id_var before/after every test
    - middleware test's `assert ... is None` now valid

  tests/api/test_logging_context_integration.py  1 test, client-based
    - uses `client` fixture (correct location)
    - filter attached to caplog.handler and removed in finally
    - asserts ERROR log carries X-Request-ID from the request

  Production code untouched (logging_config.py, middleware.py,
  main.py all correct from prior runs).

Deferred:
  P2-9  rate-limit default  - needs deployment decision
OUT
git diff --stat || true
