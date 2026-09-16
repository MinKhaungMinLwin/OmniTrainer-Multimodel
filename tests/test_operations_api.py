import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app


def build_app(database_path: Path):
    return create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database_path}",
            jwt_secret="operations-test-secret-with-at-least-32-characters",
            allow_dev_auth=True,
            auto_create_schema=True,
        )
    )


def authenticate(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    authorization = {"Authorization": f"Bearer {token}"}
    tenant_id = client.get("/api/v1/tenants", headers=authorization).json()[0]["id"]
    return {**authorization, "X-Tenant-ID": tenant_id}


def command_headers(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key, "X-Correlation-ID": "operations-lifecycle"}


def test_customer_to_issued_invoice_lifecycle_is_audited_and_idempotent(tmp_path: Path):
    database_path = tmp_path / "operations.db"
    with TestClient(build_app(database_path)) as client:
        headers = authenticate(client)
        customer = client.post(
            "/api/v1/customers",
            headers=headers,
            json={"name": "Lifecycle Customer"},
        ).json()
        create_headers = command_headers(headers, "job-create-1")
        first_job = client.post(
            "/api/v1/jobs",
            headers=create_headers,
            json={"customer_id": customer["id"], "title": "Install equipment"},
        )
        replayed_job = client.post(
            "/api/v1/jobs",
            headers=create_headers,
            json={"customer_id": customer["id"], "title": "Ignored replay body"},
        )
        assert first_job.status_code == 201
        assert replayed_job.status_code == 201
        job = first_job.json()
        assert replayed_job.json()["id"] == job["id"]

        stale_schedule = client.post(
            f"/api/v1/jobs/{job['id']}/schedule",
            headers=command_headers(headers, "schedule-stale"),
            json={
                "starts_at": "2030-01-02T09:00:00Z",
                "ends_at": "2030-01-02T10:00:00Z",
                "timezone": "America/New_York",
                "expected_version": 99,
            },
        )
        assert stale_schedule.status_code == 409

        appointment = client.post(
            f"/api/v1/jobs/{job['id']}/schedule",
            headers=command_headers(headers, "schedule-1"),
            json={
                "starts_at": "2030-01-02T09:00:00Z",
                "ends_at": "2030-01-02T10:00:00Z",
                "timezone": "America/New_York",
                "assignee": "Alex Technician",
                "expected_version": 1,
            },
        )
        assert appointment.status_code == 201
        assert appointment.json()["job_id"] == job["id"]

        completed = client.post(
            f"/api/v1/jobs/{job['id']}/complete",
            headers=command_headers(headers, "complete-1"),
            json={"expected_version": 2},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"

        drafted = client.post(
            f"/api/v1/jobs/{job['id']}/invoice",
            headers=command_headers(headers, "invoice-1"),
            json={
                "expected_job_version": 3,
                "currency": "usd",
                "lines": [
                    {"description": "Labor", "quantity": 2, "unit_price_cents": 7500},
                    {"description": "Parts", "quantity": 1, "unit_price_cents": 2500},
                ],
            },
        )
        assert drafted.status_code == 201
        invoice = drafted.json()
        assert invoice["number"] == "INV-00001"
        assert invoice["total_cents"] == 17500
        assert invoice["currency"] == "USD"

        issued = client.post(
            f"/api/v1/invoices/{invoice['id']}/issue",
            headers=command_headers(headers, "issue-1"),
            json={"expected_version": 1},
        )
        assert issued.status_code == 200
        assert issued.json()["status"] == "issued"
        assert client.get("/api/v1/invoices", headers=headers).json()["total"] == 1
        assert client.get("/api/v1/jobs", headers=headers).json()["items"][0]["status"] == "invoiced"

    connection = sqlite3.connect(database_path)
    try:
        events = connection.execute("SELECT event_type FROM outbox_events ORDER BY occurred_at, rowid").fetchall()
        receipts = connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0]
        audit = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE correlation_id = 'operations-lifecycle'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert [event[0] for event in events] == [
        "customer.created",
        "job.created",
        "job.scheduled",
        "job.completed",
        "invoice.drafted",
        "invoice.issued",
    ]
    assert receipts == 5
    assert audit == 5


def test_operations_reject_cross_tenant_resources_and_naive_times(tmp_path: Path):
    with TestClient(build_app(tmp_path / "validation.db")) as client:
        headers = authenticate(client)
        missing_customer = client.post(
            "/api/v1/jobs",
            headers=command_headers(headers, "missing-customer"),
            json={"customer_id": "outside-tenant", "title": "Not allowed"},
        )
        assert missing_customer.status_code == 404

        customer = client.post("/api/v1/customers", headers=headers, json={"name": "Time Test"}).json()
        job = client.post(
            "/api/v1/jobs",
            headers=command_headers(headers, "time-job"),
            json={"customer_id": customer["id"], "title": "Timezone test"},
        ).json()
        naive = client.post(
            f"/api/v1/jobs/{job['id']}/schedule",
            headers=command_headers(headers, "naive-time"),
            json={
                "starts_at": "2030-01-02T09:00:00",
                "ends_at": "2030-01-02T10:00:00",
                "expected_version": 1,
            },
        )
        assert naive.status_code == 422

        scheduled = client.post(
            f"/api/v1/jobs/{job['id']}/schedule",
            headers=command_headers(headers, "first-slot"),
            json={
                "starts_at": "2030-01-02T09:00:00Z",
                "ends_at": "2030-01-02T10:00:00Z",
                "assignee": "Taylor",
                "expected_version": 1,
            },
        )
        assert scheduled.status_code == 201
        second_job = client.post(
            "/api/v1/jobs",
            headers=command_headers(headers, "second-time-job"),
            json={"customer_id": customer["id"], "title": "Conflicting visit"},
        ).json()
        overlap = client.post(
            f"/api/v1/jobs/{second_job['id']}/schedule",
            headers=command_headers(headers, "overlapping-slot"),
            json={
                "starts_at": "2030-01-02T09:30:00Z",
                "ends_at": "2030-01-02T10:30:00Z",
                "assignee": "Taylor",
                "expected_version": 1,
            },
        )
        assert overlap.status_code == 409
