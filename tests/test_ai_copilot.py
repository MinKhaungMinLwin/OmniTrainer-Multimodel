from pathlib import Path

from fastapi.testclient import TestClient

from services.api.omni_api.ai_tools import (
    TOOL_REGISTRY,
    normalize_schedule_timestamp,
    normalize_tool_arguments,
)
from services.api.omni_api.ai_gateway import estimated_cost_micros
from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app


def build_app(database_path: Path):
    return create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database_path}",
            jwt_secret="test-" * 8,
            allow_dev_auth=True,
            auto_create_schema=True,
            storage_backend="local",
            ai_provider="local",
            ai_runs_per_minute=100,
        )
    )


def authenticate(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    tenant_id = client.get("/api/v1/tenants", headers=auth).json()[0]["id"]
    return {**auth, "X-Tenant-ID": tenant_id, "X-Correlation-ID": "ai-test"}


def command(headers: dict[str, str], key: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


def test_gemini_cost_estimate_uses_standard_token_rates():
    assert estimated_cost_micros("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == 2_800_000
    assert estimated_cost_micros("unknown-model", 1000, 1000) == 0


def test_schedule_tool_discards_unexpected_customer_context():
    arguments = normalize_tool_arguments(
        TOOL_REGISTRY["propose_schedule"],
        {
            "job_title": "Marketing",
            "starts_at": "2026-09-25T10:00:00",
            "ends_at": "2026-09-25T11:00:00",
            "timezone": "UTC",
            "customer_name": "James Along",
        },
    )
    assert arguments == {
        "job_title": "Marketing",
        "starts_at": "2026-09-25T10:00:00",
        "ends_at": "2026-09-25T11:00:00",
        "timezone": "UTC",
    }
    assert normalize_schedule_timestamp(arguments["starts_at"], arguments["timezone"]) == ("2026-09-25T10:00:00+00:00")
    assert normalize_schedule_timestamp("2026-09-25T10:00:00", "Asia/Bangkok") == ("2026-09-25T10:00:00+07:00")


def approve(client: TestClient, headers: dict[str, str], run: dict) -> dict:
    approval = run["approvals"][0]
    response = client.post(
        f"/api/v1/ai/approvals/{approval['id']}/decision",
        headers=headers,
        json={"decision": "approve", "reason": "Reviewed in AI operations test"},
    )
    assert response.status_code == 200
    return response.json()


def test_copilot_creates_customers_jobs_and_team_members_with_human_review(tmp_path: Path):
    with TestClient(build_app(tmp_path / "ai-writes.db")) as client:
        headers = authenticate(client)
        conversation = client.post("/api/v1/ai/conversations", headers=headers, json={}).json()

        customer_run = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "create-customer-run"),
            json={"content": "I want you to add the James X in our customer list."},
        ).json()
        assert customer_run["run"]["status"] == "waiting_approval"
        assert customer_run["tools"][0]["name"] == "create_customer"
        assert customer_run["approvals"][0]["proposed_args"]["name"] == "James X"
        approved_customer = approve(client, headers, customer_run)
        assert approved_customer["run"]["status"] == "completed"
        assert "Added customer" in approved_customer["tools"][0]["output"]["summary"]
        assert any(
            item["name"] == "James X" for item in client.get("/api/v1/customers", headers=headers).json()["items"]
        )

        job_run = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "create-job-by-name-run"),
            json={"content": 'Create job "Install smart thermostat" for customer James X'},
        ).json()
        assert job_run["approvals"][0]["proposed_args"]["customer_name"] == "James X"
        approved_job = approve(client, headers, job_run)
        assert approved_job["run"]["status"] == "completed"
        assert any(
            item["title"] == "Install smart thermostat"
            for item in client.get("/api/v1/jobs", headers=headers).json()["items"]
        )

        schedule_run = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "schedule-job-by-name-run"),
            json={
                "content": (
                    'Schedule appointment for job "Install smart thermostat" '
                    "from 2027-01-12T09:00:00Z to 2027-01-12T10:00:00Z"
                )
            },
        ).json()
        assert schedule_run["tools"][0]["name"] == "propose_schedule"
        assert schedule_run["approvals"][0]["proposed_args"]["job_title"] == "Install smart thermostat"
        approved_schedule = approve(client, headers, schedule_run)
        assert approved_schedule["run"]["status"] == "completed"

        job = next(
            item
            for item in client.get("/api/v1/jobs", headers=headers).json()["items"]
            if item["title"] == "Install smart thermostat"
        )
        completed = client.post(
            f"/api/v1/jobs/{job['id']}/complete",
            headers=command(headers, "complete-ai-job"),
            json={"expected_version": job["version"]},
        )
        assert completed.status_code == 200

        invoice_run = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "invoice-job-by-name-run"),
            json={"content": 'Draft invoice for job "Install smart thermostat" amount $250'},
        ).json()
        assert invoice_run["tools"][0]["name"] == "draft_invoice"
        assert invoice_run["approvals"][0]["proposed_args"]["amount_cents"] == 25000
        approved_invoice = approve(client, headers, invoice_run)
        assert approved_invoice["run"]["status"] == "completed"
        assert client.get("/api/v1/invoices", headers=headers).json()["total"] == 1

        technician_run = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "create-technician-run"),
            json={"content": 'Add technician "Sam Lee" with email sam.lee@example.com'},
        ).json()
        assert technician_run["tools"][0]["name"] == "create_technician"
        approved_technician = approve(client, headers, technician_run)
        assert approved_technician["run"]["status"] == "completed"
        technicians = client.get("/api/v1/technicians", headers=headers).json()
        assert any(item["name"] == "Sam Lee" and item["email"] == "sam.lee@example.com" for item in technicians)


def test_streaming_rag_tools_approval_feedback_and_replay(tmp_path: Path):
    with TestClient(build_app(tmp_path / "ai.db")) as client:
        headers = authenticate(client)
        customer = client.post(
            "/api/v1/customers",
            headers=headers,
            json={"name": "Northwind Bakery", "email": "ops@northwind.example"},
        ).json()
        knowledge = client.post(
            "/api/v1/ai/knowledge",
            headers=headers,
            json={
                "title": "Cancellation policy",
                "content": "Appointments may be cancelled without charge at least 24 hours before arrival.",
                "source_uri": "https://example.test/policies/cancellation",
                "access_roles": ["owner", "dispatcher"],
            },
        )
        assert knowledge.status_code == 201
        conversation = client.post(
            "/api/v1/ai/conversations", headers=headers, json={"title": "Operations help"}
        ).json()

        rag = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "rag-run"),
            json={"content": "What is our cancellation policy?"},
        )
        assert rag.status_code == 201
        rag_run = rag.json()
        assert rag_run["run"]["status"] == "completed"
        assert [event["sequence"] for event in rag_run["events"]] == list(range(1, len(rag_run["events"]) + 1))
        assert rag_run["tools"][0]["name"] == "search_knowledge"
        assert rag_run["tools"][0]["output"]["citations"][0]["title"] == "Cancellation policy"
        stream = client.get(f"/api/v1/ai/runs/{rag_run['run']['id']}/stream?after=2", headers=headers)
        assert stream.status_code == 200
        assert "event: tool_running" in stream.text
        assert "id: 1\n" not in stream.text

        customer_run = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "customer-run"),
            json={"content": "Find customer Northwind"},
        ).json()
        assert customer_run["tools"][0]["output"]["data"]["customers"][0]["id"] == customer["id"]
        multi_step = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "multi-run"),
            json={"content": "Find customer Northwind and check availability"},
        ).json()
        assert len(multi_step["tools"]) == 2
        assert all(tool["status"] == "completed" for tool in multi_step["tools"])

        proposed = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "write-run"),
            json={"content": f'Create job "Replace oven sensor" for customer {customer["id"]}'},
        )
        assert proposed.status_code == 201
        proposed_run = proposed.json()
        assert proposed_run["run"]["status"] == "waiting_approval"
        assert proposed_run["tools"][0]["status"] == "waiting_approval"
        approval = proposed_run["approvals"][0]
        approved = client.post(
            f"/api/v1/ai/approvals/{approval['id']}/decision",
            headers=headers,
            json={
                "decision": "edit",
                "reason": "Clarified the on-site task",
                "arguments": {
                    "customer_id": customer["id"],
                    "title": "Replace oven temperature sensor",
                    "description": "Approved by dispatch",
                },
            },
        )
        assert approved.status_code == 200
        approved_run = approved.json()
        assert approved_run["run"]["status"] == "completed", approved_run
        assert approved_run["approvals"][0]["status"] == "edited"
        assert any(event["event_type"] == "corrected_result" for event in approved_run["events"])
        jobs = client.get("/api/v1/jobs", headers=headers).json()["items"]
        assert any(item["title"] == "Replace oven temperature sensor" for item in jobs)

        replay = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "write-run"),
            json={"content": "This content must not create another run"},
        )
        assert replay.json()["run"]["id"] == proposed_run["run"]["id"]

        feedback = client.post(
            f"/api/v1/ai/runs/{approved_run['run']['id']}/feedback",
            headers=headers,
            json={"rating": "up", "category": "helpful", "comment": "Correct after review"},
        )
        assert feedback.status_code == 201
        assert feedback.json()["run_context"]["policy_version"] == "approval-v1"


def test_rejection_cancellation_extraction_and_knowledge_deletion(tmp_path: Path):
    with TestClient(build_app(tmp_path / "review.db")) as client:
        headers = authenticate(client)
        conversation = client.post("/api/v1/ai/conversations", headers=headers, json={}).json()
        proposed = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "message-run"),
            json={"content": "Send message to +15550199: Your appointment is confirmed"},
        ).json()
        assert proposed["approvals"][0]["proposed_args"]["recipient"] == "+15550199"
        rejected = client.post(
            f"/api/v1/ai/approvals/{proposed['approvals'][0]['id']}/decision",
            headers=headers,
            json={"decision": "reject", "reason": "Wrong recipient"},
        )
        assert rejected.json()["run"]["status"] == "completed"
        assert rejected.json()["tools"][0]["status"] == "rejected"

        waiting = client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/runs",
            headers=command(headers, "cancel-run"),
            json={"content": "Send message to +15550200: Test"},
        ).json()
        cancelled = client.post(f"/api/v1/ai/runs/{waiting['run']['id']}/cancel", headers=headers).json()
        assert cancelled["run"]["status"] == "cancelled"
        resumed_events = client.get(f"/api/v1/ai/runs/{waiting['run']['id']}/events?after=2", headers=headers).json()
        assert all(event["sequence"] > 2 for event in resumed_events)

        extraction = client.post(
            "/api/v1/ai/extractions",
            headers=headers,
            json={"schema_name": "job_request", "input_text": "The kitchen tap is leaking."},
        )
        assert extraction.status_code == 201
        assert extraction.json()["status"] == "pending_review"
        reviewed = client.post(
            f"/api/v1/ai/extractions/{extraction.json()['id']}/review",
            headers=headers,
            json={
                "decision": "correct",
                "corrected_fields": {"summary": "Kitchen tap leak", "urgency": "normal"},
                "reason": "Normalized summary",
            },
        )
        assert reviewed.json()["status"] == "corrected"

        document = client.post(
            "/api/v1/ai/knowledge",
            headers=headers,
            json={"title": "Private policy", "content": "Unique retention policy phrase."},
        ).json()
        assert client.get("/api/v1/ai/knowledge/search?query=retention", headers=headers).json()
        removed = client.delete(f"/api/v1/ai/knowledge/{document['id']}", headers=headers)
        assert removed.status_code == 204
        assert client.get("/api/v1/ai/knowledge/search?query=retention", headers=headers).json() == []
