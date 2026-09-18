from pathlib import Path

from fastapi.testclient import TestClient

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
        assert approved_run["run"]["status"] == "completed"
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
