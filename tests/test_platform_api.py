import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app


def build_test_app(database_path: Path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        jwt_secret="test-" * 8,
        allow_dev_auth=True,
        auto_create_schema=True,
    )
    return create_app(settings)


def authenticate(client: TestClient, email: str = "owner@omni.example") -> tuple[str, str]:
    token_response = client.post("/api/v1/dev/token", json={"email": email})
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    tenants_response = client.get("/api/v1/tenants", headers=headers)
    assert tenants_response.status_code == 200
    tenant_id = tenants_response.json()[0]["id"]
    return token, tenant_id


def test_health_and_authenticated_identity(tmp_path: Path):
    app = build_test_app(tmp_path / "identity.db")
    with TestClient(app, raise_server_exceptions=True) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        token, _ = authenticate(client)
        response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["email"] == "owner@omni.example"


def test_customer_vertical_slice_is_tenant_scoped_and_audited(tmp_path: Path):
    database_path = tmp_path / "customers.db"
    app = build_test_app(database_path)
    with TestClient(app, raise_server_exceptions=True) as client:
        token, tenant_id = authenticate(client)
        auth_headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": tenant_id,
            "X-Correlation-ID": "customer-test-correlation",
        }
        create_response = client.post(
            "/api/v1/customers",
            headers=auth_headers,
            json={"name": "Acme Services", "email": "ops@acme.example", "phone": "+1 555 0100"},
        )
        assert create_response.status_code == 201
        assert create_response.headers["X-Correlation-ID"] == "customer-test-correlation"
        assert create_response.json()["tenant_id"] == tenant_id

        list_response = client.get("/api/v1/customers", headers=auth_headers)
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 1
        assert list_response.json()["items"][0]["name"] == "Acme Services"

        forbidden_response = client.get(
            "/api/v1/customers",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "not-an-authorized-tenant"},
        )
        assert forbidden_response.status_code == 403

    connection = sqlite3.connect(database_path)
    try:
        audit = connection.execute(
            "SELECT action, tenant_id, correlation_id FROM audit_events WHERE resource_type = 'customer'"
        ).fetchone()
    finally:
        connection.close()
    assert audit == ("customer.created", tenant_id, "customer-test-correlation")


def test_customer_api_requires_authentication(tmp_path: Path):
    with TestClient(build_test_app(tmp_path / "auth.db"), raise_server_exceptions=True) as client:
        response = client.get("/api/v1/customers", headers={"X-Tenant-ID": "tenant"})
        assert response.status_code == 401


def test_customer_creation_enforces_role(tmp_path: Path):
    with TestClient(build_test_app(tmp_path / "role.db"), raise_server_exceptions=True) as client:
        token, tenant_id = authenticate(client, "technician@omni.example")
        response = client.post(
            "/api/v1/customers",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant_id},
            json={"name": "Not permitted"},
        )
        assert response.status_code == 403
