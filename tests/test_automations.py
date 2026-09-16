import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app
from services.worker.omni_worker.automation_dispatcher import dispatch_automation_work
from services.worker.omni_worker.jobs import JobEnvelope


class FakeDispatchRedis:
    def __init__(self):
        self.claims: set[str] = set()
        self.messages: list[str] = []

    async def set(self, key: str, _: str, nx: bool, ex: int) -> bool:
        assert nx and ex > 0
        if key in self.claims:
            return False
        self.claims.add(key)
        return True

    async def rpush(self, queue: str, message: str) -> None:
        assert queue == "omni:jobs"
        self.messages.append(message)


def build_client(path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                environment="test",
                database_url=f"sqlite+aiosqlite:///{path}",
                jwt_secret="test-secret-with-at-least-thirty-two-characters",
                allow_dev_auth=True,
                auto_create_schema=True,
            )
        ),
        raise_server_exceptions=True,
    )


def auth(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    bearer = {"Authorization": f"Bearer {token}"}
    tenant_id = client.get("/api/v1/tenants", headers=bearer).json()[0]["id"]
    return {**bearer, "X-Tenant-ID": tenant_id}


def install(client: TestClient, headers: dict[str, str], key: str) -> dict:
    response = client.post(f"/api/v1/automations/templates/{key}/install", headers=headers, json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_template_library_shadow_run_and_idempotent_trigger(tmp_path: Path):
    with build_client(tmp_path / "templates.db") as client:
        headers = auth(client)
        templates = client.get("/api/v1/automations/templates", headers=headers)
        assert templates.status_code == 200
        assert {item["key"] for item in templates.json()} == {
            "lead_intake",
            "appointment_reminder",
            "missed_call_follow_up",
            "job_completion_summary",
            "invoice_draft",
            "overdue_invoice_follow_up",
            "review_escalation",
        }

        definition = install(client, headers, "missed_call_follow_up")
        test_run = client.post(
            f"/api/v1/automations/definitions/{definition['id']}/test",
            headers=headers,
            json={"payload": {"caller": "+15550100"}},
        )
        assert test_run.status_code == 200, test_run.text
        assert test_run.json()["status"] == "shadowed"
        assert test_run.json()["output"]["side_effects"] is False

        activated = client.post(
            f"/api/v1/automations/definitions/{definition['id']}/activate",
            headers=headers,
            json={"mode": "shadow"},
        )
        assert activated.json()["status"] == "active"
        trigger = {
            "trigger_type": "call.missed",
            "source": "call",
            "idempotency_key": "call-1",
            "payload": {"caller": "+15550100"},
        }
        first = client.post("/api/v1/automations/triggers", headers=headers, json=trigger)
        second = client.post("/api/v1/automations/triggers", headers=headers, json=trigger)
        assert first.status_code == 200
        assert first.json()[0]["status"] == "shadowed"
        assert second.json()[0]["id"] == first.json()[0]["id"]


def test_production_approval_edit_execution_and_compensation(tmp_path: Path):
    database = tmp_path / "approval.db"
    with build_client(database) as client:
        headers = auth(client)
        definition = install(client, headers, "appointment_reminder")
        client.post(
            f"/api/v1/automations/definitions/{definition['id']}/activate",
            headers=headers,
            json={"mode": "production"},
        )
        response = client.post(
            "/api/v1/automations/triggers",
            headers=headers,
            json={
                "trigger_type": "appointment.upcoming",
                "source": "schedule",
                "idempotency_key": "appointment-1",
                "payload": {"recipient": "+15550100", "starts_at": "tomorrow at 9"},
            },
        )
        run = response.json()[0]
        assert run["status"] == "waiting_approval"
        actions = run["approval"]["proposed_actions"]
        actions[0]["arguments"]["body"] = "Edited reminder"
        approved = client.post(
            f"/api/v1/automations/approvals/{run['approval']['id']}/decision",
            headers=headers,
            json={"decision": "approve", "actions": actions, "reason": "Reviewed"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "succeeded"
        assert approved.json()["changed_resources"][0]["type"] == "outbound_message"

        compensated = client.post(f"/api/v1/automations/runs/{run['id']}/compensate", headers=headers)
        assert compensated.status_code == 200
        assert compensated.json()["status"] == "compensated"

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT status, body FROM outbound_messages").fetchone() == (
            "cancelled",
            "Edited reminder",
        )
    finally:
        connection.close()


def test_versioning_kill_switch_dead_letter_and_replay(tmp_path: Path):
    with build_client(tmp_path / "controls.db") as client:
        headers = auth(client)
        create = client.post(
            "/api/v1/automations",
            headers=headers,
            json={
                "name": "Broken lead action",
                "description": "Exercises retry and dead-letter handling",
                "mode": "production",
                "trigger_type": "lead.qualified",
                "conditions": [],
                "steps": [
                    {
                        "kind": "action",
                        "operation": "create_job",
                        "config": {"customer_id": "{{payload.customer_id}}", "title": "Lead"},
                    }
                ],
                "approval_rule": {"mode": "never"},
                "rate_limit_per_hour": 5,
                "max_attempts": 1,
            },
        )
        assert create.status_code == 201, create.text
        definition = create.json()
        update = client.put(
            f"/api/v1/automations/definitions/{definition['id']}",
            headers=headers,
            json={
                "description": "Version two",
                "mode": "production",
                "trigger_type": "lead.qualified",
                "conditions": [],
                "steps": definition["version"]["steps"],
                "approval_rule": {"mode": "never"},
                "rate_limit_per_hour": 5,
                "max_attempts": 1,
            },
        )
        assert update.json()["current_version"] == 2
        client.post(
            f"/api/v1/automations/definitions/{definition['id']}/activate",
            headers=headers,
            json={"mode": "production"},
        )
        trigger = client.post(
            "/api/v1/automations/triggers",
            headers=headers,
            json={
                "trigger_type": "lead.qualified",
                "source": "inbound_message",
                "idempotency_key": "bad-lead",
                "payload": {"customer_id": "missing"},
            },
        )
        run = trigger.json()[0]
        assert run["status"] == "dead_letter"
        retried = client.post(f"/api/v1/automations/runs/{run['id']}/retry", headers=headers)
        assert retried.status_code == 200
        assert retried.json()["status"] == "dead_letter"
        replay = client.post(f"/api/v1/automations/runs/{run['id']}/replay", headers=headers)
        assert replay.status_code == 201
        assert replay.json()["replay_of_run_id"] == run["id"]
        assert replay.json()["version"] == 2

        killed = client.post(
            f"/api/v1/automations/definitions/{definition['id']}/kill",
            headers=headers,
            json={"reason": "Emergency stop"},
        )
        assert killed.json()["status"] == "killed"
        reactivate = client.post(
            f"/api/v1/automations/definitions/{definition['id']}/activate",
            headers=headers,
            json={"mode": "shadow"},
        )
        assert reactivate.status_code == 409

        second = install(client, headers, "review_escalation")
        client.post(
            f"/api/v1/automations/definitions/{second['id']}/activate",
            headers=headers,
            json={"mode": "shadow"},
        )
        bulk = client.post(
            "/api/v1/automations/bulk/control",
            headers=headers,
            json={"definition_ids": [second["id"]], "action": "pause", "reason": "Maintenance"},
        )
        assert bulk.status_code == 200
        assert bulk.json()[0]["status"] == "paused"

        metrics = client.get("/api/v1/automations/metrics/summary", headers=headers)
        assert metrics.status_code == 200
        assert metrics.json()["dead_letter"] == 2


async def test_worker_dispatches_transactional_domain_events(tmp_path: Path, monkeypatch):
    database = tmp_path / "dispatcher.db"
    with build_client(database) as client:
        headers = auth(client)
        definition = client.post(
            "/api/v1/automations",
            headers=headers,
            json={
                "name": "Customer created observer",
                "trigger_type": "customer.created",
                "conditions": [],
                "steps": [{"kind": "ai", "operation": "summarize", "config": {"source": "name"}}],
            },
        ).json()
        client.post(
            f"/api/v1/automations/definitions/{definition['id']}/activate",
            headers=headers,
            json={"mode": "shadow"},
        )
        customer = client.post(
            "/api/v1/customers",
            headers=headers,
            json={"name": "Dispatch Test Customer"},
        )
        assert customer.status_code == 201

    monkeypatch.setenv("OMNI_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    redis = FakeDispatchRedis()
    assert await dispatch_automation_work(redis) == 1
    envelope = JobEnvelope.model_validate_json(redis.messages[0])
    assert envelope.type == "automation.execute"
    assert envelope.tenant_id == definition["tenant_id"]
