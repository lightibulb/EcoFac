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
    lines.extend(f"2026-01-01T{h:02d}:00:00+00:00,b1,m1,100.0,kwh" for h in range(24))
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
            rec for rec in caplog.records if "Post-import analysis failed" in rec.getMessage()
        ]
        assert matching, "expected the analysis-failure ERROR log"
        assert matching[0].request_id == "req-test-p28"
    finally:
        caplog.handler.removeFilter(filter_obj)
