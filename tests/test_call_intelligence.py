from pathlib import Path

from fastapi.testclient import TestClient

from services.api.omni_api.config import Settings
from services.api.omni_api.main import create_app


def settings(path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{path}",
        jwt_secret="test-" * 8,
        allow_dev_auth=True,
        auto_create_schema=True,
        attachment_dir=str(path.parent / "objects"),
    )


def auth(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    bearer = {"Authorization": f"Bearer {token}"}
    tenant_id = client.get("/api/v1/tenants", headers=bearer).json()[0]["id"]
    return {**bearer, "X-Tenant-ID": tenant_id}


def enable_voice(client: TestClient, headers: dict[str, str]) -> None:
    config = client.get("/api/v1/voice/config", headers=headers).json()
    config["enabled"] = True
    config["allowed_regions"] = ["local"]
    config["allowed_hours"] = {
        "timezone": "UTC",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "start": "00:00",
        "end": "23:59",
    }
    assert client.put("/api/v1/voice/config", headers=headers, json=config).status_code == 200


def simulate(client: TestClient, headers: dict[str, str], reason: str) -> dict:
    response = client.post(
        "/api/v1/voice/simulations",
        headers=headers,
        json={
            "caller": "+15550123",
            "turns": [
                {"text": "yes"},
                {"text": "Alex Caller"},
                {"text": reason},
                {"text": "yes"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_call_pipeline_search_dashboard_correction_and_reconciliation(tmp_path: Path):
    with TestClient(create_app(settings(tmp_path / "intelligence.db")), raise_server_exceptions=True) as client:
        headers = auth(client)
        enable_voice(client, headers)
        call = simulate(
            client,
            headers,
            "My heating stopped. Email alex@example.com or call +1 555 777 9999.",
        )

        processed = client.post(f"/api/v1/intelligence/calls/{call['id']}/process", headers=headers)
        assert processed.status_code == 200, processed.text
        detail = processed.json()
        assert detail["pipeline"]["status"] == "completed"
        assert detail["pipeline"]["checkpoints"] == [
            "transcript_finalized",
            "pii_redacted",
            "conversation_extracted",
            "search_indexed",
            "fact_modeled",
            "reconciled",
        ]
        assert detail["intelligence"]["topic"] == "heating_cooling"
        assert detail["intelligence"]["status"] == "accepted"
        assert "alex@example.com" not in detail["transcript"]["redacted_text"]
        assert "[EMAIL]" in detail["transcript"]["redacted_text"]
        assert "[PHONE]" in detail["transcript"]["redacted_text"]
        assert detail["fact"]["consented"] is True
        assert detail["fact"]["transcript_segments"] >= 4
        assert detail["reconciliations"][0]["complete"] is True

        replayed = client.post(f"/api/v1/intelligence/calls/{call['id']}/process", headers=headers).json()
        assert replayed["intelligence"]["id"] == detail["intelligence"]["id"]

        search = client.get("/api/v1/intelligence/calls?q=heating", headers=headers).json()
        assert search[0]["call_id"] == call["id"]
        assert client.get("/api/v1/intelligence/calls?q=alex@example.com", headers=headers).json() == []

        dashboard = client.get("/api/v1/intelligence/dashboard", headers=headers).json()
        assert dashboard["total_calls"] == 1
        assert dashboard["containment_rate"] == 1
        assert dashboard["reconciliation_rate"] == 1
        assert dashboard["pipeline_success_rate"] == 1
        assert dashboard["topics"] == [{"key": "heating_cooling", "count": 1}]
        definitions = client.get("/api/v1/intelligence/metric-definitions", headers=headers).json()
        assert {item["key"] for item in definitions} >= {"volume", "containment_rate", "estimated_cost"}

        segments = detail["transcript"]["segments"]
        reason_segment = next(item for item in segments if item["speaker"] == "caller" and "heating" in item["text"])
        reason_segment["text"] = "The corrected issue is a leaking kitchen pipe."
        corrected = client.post(
            f"/api/v1/intelligence/calls/{call['id']}/transcript-corrections",
            headers=headers,
            json={"segments": segments, "reason": "Reviewer listened to the recording"},
        )
        assert corrected.status_code == 200, corrected.text
        corrected_detail = corrected.json()
        assert corrected_detail["transcript"]["version"] == 2
        assert corrected_detail["transcript"]["source_revision_id"] == detail["transcript"]["id"]
        assert corrected_detail["intelligence"]["version"] == 2
        assert corrected_detail["intelligence"]["topic"] == "plumbing"

        mismatch = client.post(
            f"/api/v1/intelligence/calls/{call['id']}/reconcile",
            headers=headers,
            json={
                "provider_snapshot_id": "provider-daily-1",
                "provider_status": "failed",
                "provider_duration_ms": corrected_detail["fact"]["duration_ms"] + 5000,
            },
        )
        assert mismatch.status_code == 200
        assert mismatch.json()["complete"] is False
        assert set(mismatch.json()["discrepancies"]) == {"status_mismatch", "duration_mismatch"}


def test_low_confidence_intelligence_enters_human_review(tmp_path: Path):
    with TestClient(create_app(settings(tmp_path / "review.db")), raise_server_exceptions=True) as client:
        headers = auth(client)
        enable_voice(client, headers)
        call = client.post(
            "/api/v1/voice/simulations",
            headers=headers,
            json={"turns": [{"text": "no"}]},
        ).json()
        detail = client.post(f"/api/v1/intelligence/calls/{call['id']}/process", headers=headers).json()
        assert detail["intelligence"]["status"] == "pending_review"
        reviewed = client.post(
            f"/api/v1/intelligence/calls/{call['id']}/review",
            headers=headers,
            json={
                "decision": "correct",
                "corrected_fields": {"topic": "consent_declined"},
                "reason": "Quality reviewer verified the call",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["status"] == "corrected"
        assert reviewed.json()["topic"] == "consent_declined"
        assert reviewed.json()["corrected_fields"] == {"topic": "consent_declined"}
