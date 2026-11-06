# Copyright 2026 Lightibulb

"""API integration tests."""


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_buildings(client):
    response = client.get("/api/v1/buildings")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == "b1"


def test_get_building_404(client):
    response = client.get("/api/v1/buildings/nonexistent")
    assert response.status_code == 404


def test_upload_csv(client):
    csv_content = (
        "timestamp,building_id,meter_id,energy_value,unit\n2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n"
    )
    files = {"file": ("test.csv", csv_content, "text/csv")}
    data = {"source_id": "s1", "batch_id": "ib1"}

    response = client.post("/api/v1/imports", files=files, data=data)
    assert response.status_code == 200
    assert response.json()["accepted_rows"] == 1


def test_import_too_large(client):
    huge_content = (
        "timestamp,building_id,meter_id,energy_value,unit\n"
        + "2026-01-01T12:00:00Z,b1,m1,10.5,kWh\n" * 200000
    )
    files = {"file": ("huge.csv", huge_content, "text/csv")}
    data = {"source_id": "s1", "batch_id": "ib1"}

    response = client.post("/api/v1/imports", files=files, data=data)
    assert response.status_code == 413
