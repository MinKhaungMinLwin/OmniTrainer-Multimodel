from pathlib import Path

from fastapi.testclient import TestClient

from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app


def build_app(database_path: Path, attachment_dir: Path):
    return create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database_path}",
            jwt_secret="test-" * 8,
            allow_dev_auth=True,
            auto_create_schema=True,
            storage_backend="local",
            attachment_dir=str(attachment_dir),
        )
    )


def authenticate(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    tenant_id = client.get("/api/v1/tenants", headers=auth).json()[0]["id"]
    return {**auth, "X-Tenant-ID": tenant_id, "X-Correlation-ID": "mvp-test"}


def command(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


def test_complete_operations_mvp(tmp_path: Path):
    with TestClient(build_app(tmp_path / "mvp.db", tmp_path / "attachments")) as client:
        headers = authenticate(client)
        customer = client.post(
            "/api/v1/customers",
            headers=headers,
            json={"name": "MVP Customer", "email": "initial@example.com"},
        ).json()
        updated = client.put(
            f"/api/v1/customers/{customer['id']}",
            headers=headers,
            json={
                "name": "MVP Customer Updated",
                "email": "updated@example.com",
                "phone": "+1 555 0100",
                "external_ref": None,
                "notes": "Priority account",
                "expected_version": 1,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2

        contact = client.post(
            f"/api/v1/customers/{customer['id']}/contacts",
            headers=headers,
            json={"name": "Casey", "email": "casey@example.com", "is_primary": True},
        )
        assert contact.status_code == 201
        location = client.post(
            f"/api/v1/customers/{customer['id']}/locations",
            headers=headers,
            json={
                "label": "Main office",
                "address_line1": "1 Main Street",
                "city": "Bangkok",
                "country": "TH",
                "timezone": "Asia/Bangkok",
            },
        ).json()
        detail = client.get(f"/api/v1/customers/{customer['id']}", headers=headers)
        assert detail.status_code == 200
        assert len(detail.json()["contacts"]) == 1
        assert len(detail.json()["locations"]) == 1
        assert len(detail.json()["activity"]) >= 3

        technician = client.post(
            "/api/v1/technicians",
            headers=headers,
            json={"name": "Taylor Tech", "email": "taylor@example.com", "timezone": "UTC"},
        ).json()
        availability = client.post(
            f"/api/v1/technicians/{technician['id']}/availability",
            headers=headers,
            json={"weekday": 0, "start_minute": 8 * 60, "end_minute": 17 * 60},
        )
        assert availability.status_code == 201

        job = client.post(
            "/api/v1/jobs",
            headers=command(headers, "mvp-job"),
            json={
                "customer_id": customer["id"],
                "location_id": location["id"],
                "title": "Install system",
            },
        ).json()
        appointment = client.post(
            f"/api/v1/jobs/{job['id']}/schedule",
            headers=command(headers, "mvp-schedule"),
            json={
                "starts_at": "2030-01-07T09:00:00Z",
                "ends_at": "2030-01-07T10:00:00Z",
                "timezone": "UTC",
                "technician_id": technician["id"],
                "expected_version": 1,
            },
        )
        assert appointment.status_code == 201
        assert appointment.json()["assignee"] == "Taylor Tech"
        appointment = client.post(
            f"/api/v1/appointments/{appointment.json()['id']}/reschedule",
            headers=headers,
            json={
                "starts_at": "2030-01-07T10:00:00Z",
                "ends_at": "2030-01-07T11:00:00Z",
                "timezone": "UTC",
                "technician_id": technician["id"],
                "expected_version": 1,
            },
        )
        assert appointment.status_code == 200
        assert appointment.json()["version"] == 2

        note = client.post(
            f"/api/v1/jobs/{job['id']}/notes",
            headers=headers,
            json={"body": "Bring replacement filter"},
        )
        assert note.status_code == 201
        upload = client.post(
            f"/api/v1/jobs/{job['id']}/attachments",
            headers=headers,
            files={"file": ("photo.txt", b"attachment-content", "text/plain")},
        )
        assert upload.status_code == 201
        download = client.get(f"/api/v1/attachments/{upload.json()['id']}/download", headers=headers)
        assert download.content == b"attachment-content"
        assert len(client.get(f"/api/v1/jobs/{job['id']}/notes", headers=headers).json()) == 1

        completed = client.post(
            f"/api/v1/jobs/{job['id']}/complete",
            headers=command(headers, "mvp-complete"),
            json={"expected_version": 2},
        ).json()
        history = client.get(f"/api/v1/jobs/{job['id']}/history", headers=headers).json()
        assert [item["to_status"] for item in history] == ["draft", "scheduled", "completed"]
        invoice = client.post(
            f"/api/v1/jobs/{job['id']}/invoice",
            headers=command(headers, "mvp-invoice"),
            json={
                "expected_job_version": completed["version"],
                "currency": "USD",
                "lines": [{"description": "Initial", "quantity": 1, "unit_price_cents": 10000}],
            },
        ).json()
        invoice = client.put(
            f"/api/v1/invoices/{invoice['id']}/lines",
            headers=headers,
            json={
                "expected_version": 1,
                "lines": [
                    {"description": "Labor", "quantity": 2, "unit_price_cents": 6000},
                    {"description": "Parts", "quantity": 1, "unit_price_cents": 3000},
                ],
            },
        ).json()
        assert invoice["total_cents"] == 15000
        issued = client.post(
            f"/api/v1/invoices/{invoice['id']}/issue",
            headers=command(headers, "mvp-issue"),
            json={"expected_version": invoice["version"]},
        ).json()
        partial = client.post(
            f"/api/v1/invoices/{invoice['id']}/payments",
            headers=command(headers, "mvp-payment-1"),
            json={"amount_cents": 5000, "method": "card", "external_ref": "pay-1"},
        )
        assert partial.status_code == 201
        replayed = client.post(
            f"/api/v1/invoices/{invoice['id']}/payments",
            headers=command(headers, "mvp-payment-1"),
            json={"amount_cents": 5000, "method": "card", "external_ref": "pay-1"},
        )
        assert replayed.status_code == 201
        assert replayed.json()["id"] == partial.json()["id"]
        remaining = client.post(
            f"/api/v1/invoices/{invoice['id']}/payments",
            headers=command(headers, "mvp-payment-2"),
            json={"amount_cents": 10000, "method": "cash", "external_ref": "pay-2"},
        )
        assert remaining.status_code == 201
        listed = client.get("/api/v1/invoices", headers=headers).json()["items"][0]
        assert listed["payment_status"] == "paid"
        assert listed["paid_cents"] == 15000
        pdf = client.get(f"/api/v1/invoices/{invoice['id']}/pdf", headers=headers)
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        cannot_void = client.post(
            f"/api/v1/invoices/{invoice['id']}/void",
            headers=command(headers, "mvp-void-paid"),
            json={"expected_version": issued["version"] + 2},
        )
        assert cannot_void.status_code == 409
        cannot_delete = client.delete(f"/api/v1/customers/{customer['id']}", headers=headers)
        assert cannot_delete.status_code == 409


def test_technician_availability_is_enforced(tmp_path: Path):
    with TestClient(build_app(tmp_path / "availability.db", tmp_path / "files")) as client:
        headers = authenticate(client)
        customer = client.post("/api/v1/customers", headers=headers, json={"name": "Customer"}).json()
        technician = client.post(
            "/api/v1/technicians",
            headers=headers,
            json={"name": "Day Tech", "email": "day@example.com", "timezone": "UTC"},
        ).json()
        client.post(
            f"/api/v1/technicians/{technician['id']}/availability",
            headers=headers,
            json={"weekday": 0, "start_minute": 540, "end_minute": 1020},
        )
        job = client.post(
            "/api/v1/jobs",
            headers=command(headers, "availability-job"),
            json={"customer_id": customer["id"], "title": "After hours"},
        ).json()
        response = client.post(
            f"/api/v1/jobs/{job['id']}/schedule",
            headers=command(headers, "after-hours"),
            json={
                "starts_at": "2030-01-07T18:00:00Z",
                "ends_at": "2030-01-07T19:00:00Z",
                "timezone": "UTC",
                "technician_id": technician["id"],
                "expected_version": 1,
            },
        )
        assert response.status_code == 409
