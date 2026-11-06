# Copyright 2026 Lightibulb

"""Tests for the dashboard endpoints."""


def test_dashboard_stats(client):
    """Verify the stats endpoint returns a valid JSON structure."""
    response = client.get("/api/v1/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert "buildings" in data
    assert "meters" in data
    assert "total_incidents" in data
    assert "open_incidents" in data


def test_dashboard_chart_data(client):
    """Verify the chart data endpoint returns a valid JSON structure."""
    response = client.get("/api/v1/dashboard/chart-data")
    assert response.status_code == 200
    data = response.json()
    assert "labels" in data
    assert "data" in data


def test_dashboard_root(client):
    """Verify the HTML dashboard root loads successfully."""
    response = client.get("/")
    assert response.status_code == 200
    assert "EcoFac" in response.text
