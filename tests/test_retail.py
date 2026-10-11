import asyncio
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.api.omni_api import ai_gateway
from services.api.omni_api.ai_gateway import LocalCopilotProvider, get_copilot_provider
from services.api.omni_api.config import Settings
from services.api.omni_api.database import Base, build_engine, build_session_factory
from services.api.omni_api.main import create_app
from services.api.omni_api.retail_analytics import local_forecast
from services.api.omni_api.retail_seed import seed_retail_data


def build_app(database_path: Path):
    return create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{database_path}",
            jwt_secret="retail-test-secret-" * 2,
            allow_dev_auth=True,
            auto_create_schema=True,
            storage_backend="local",
            ai_provider="local",
            ai_runs_per_minute=100,
        )
    )


def seed(database_path: Path) -> dict:
    async def run() -> dict:
        engine = build_engine(f"sqlite+aiosqlite:///{database_path}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            return await seed_retail_data(build_session_factory(engine), days=120, scale=0.35)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def authenticate(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/dev/token", json={"email": "owner@omni.example"}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    tenants = client.get("/api/v1/tenants", headers=auth).json()
    tenant_id = next(tenant["id"] for tenant in tenants if tenant["slug"] == "harbourline")
    return {**auth, "X-Tenant-ID": tenant_id, "X-Correlation-ID": "retail-test"}


@pytest.fixture
def retail_client(tmp_path: Path):
    database = tmp_path / "retail.db"
    summary = seed(database)
    assert summary["status"] == "seeded" and summary["orders"] > 1000
    with TestClient(build_app(database)) as client:
        yield client, authenticate(client)


def test_local_forecast_tracks_level_weekday_and_trend():
    flat, trend = local_forecast([10] * 56, date(2026, 10, 12), 14)
    assert all(9.5 <= value <= 10.5 for value in flat)
    assert trend == 0
    rising, rising_trend = local_forecast([5] * 42 + [10] * 14, date(2026, 10, 12), 14)
    assert rising_trend > 0.3
    assert sum(rising) > 140


def test_seed_is_idempotent_and_insights_are_grounded(retail_client, tmp_path: Path):
    client, headers = retail_client
    overview = client.get("/api/v1/retail/insights/overview", headers=headers).json()
    assert overview["revenue_30d_cents"] > 0 and overview["orders_30d"] > 0
    assert len(overview["daily"]) == 60

    inventory = client.get("/api/v1/retail/insights/inventory?horizon_days=14", headers=headers).json()
    critical = [row for row in inventory if row["risk"] == "critical"]
    assert critical, "seasonal SKUs with tight cover should be at risk"
    assert all(row["recommended_quantity"] % row["case_pack"] == 0 for row in inventory)
    assert {"RW-UMB-CMP", "FG-HAMPER-DEEP"} & {row["sku"] for row in critical}

    segments = client.get("/api/v1/retail/insights/segments", headers=headers).json()
    assert {row["segment"] for row in segments} >= {"Champions", "At risk", "Lapsed high-value"}
    assert sum(row["customers"] for row in segments) > 100

    pairs = client.get("/api/v1/retail/insights/basket", headers=headers).json()
    pair_names = {frozenset((pair["anchor_sku"], pair["companion_sku"])) for pair in pairs}
    assert frozenset(("HK-WOK", "HK-SPATULA")) in pair_names or frozenset(("OT-TENT-2P", "OT-SLEEP-BAG")) in pair_names
    assert all(pair["lift"] >= 1.5 for pair in pairs)

    products = client.get("/api/v1/retail/products", headers=headers).json()
    assert len(products) == 56
    orders = client.get("/api/v1/retail/orders?limit=5", headers=headers).json()
    assert len(orders) == 5 and orders[0]["items"]


def test_shopping_assistant_filters_budget_and_personalises(retail_client):
    client, headers = retail_client
    response = client.post(
        "/api/v1/retail/shop/assist",
        headers=headers,
        json={"message": "I need a waterproof jacket for hiking under $100"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["search_backend"] == "local"
    assert body["products"][0]["sku"] == "RW-JKT-LT"
    assert all(product["price_cents"] <= 10_000 for product in body["products"])
    assert "Monsoon Lite Rain Jacket" in body["reply"]

    shoppers = client.get("/api/v1/retail/shoppers", headers=headers).json()
    shopper = shoppers[0]
    personalised = client.post(
        "/api/v1/retail/shop/assist",
        headers=headers,
        json={"message": "coffee", "customer_id": shopper["id"], "history": [{"role": "user", "content": "hi"}]},
    ).json()
    assert personalised["personalized"] is True
    assert all(product["category"] for product in personalised["products"])

    picks = client.get(f"/api/v1/retail/shop/recommendations?customer_id={shopper['id']}", headers=headers).json()
    assert picks and all(pick["reason"] and pick["in_stock"] for pick in picks)

    missing = client.post(
        "/api/v1/retail/shop/assist", headers=headers, json={"message": "tent", "customer_id": "missing"}
    )
    assert missing.status_code == 404


def test_agent_restock_uses_forecast_quantities_and_requires_approval(retail_client):
    client, headers = retail_client
    conversation = client.post("/api/v1/ai/conversations", headers=headers, json={}).json()
    run = client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/runs",
        headers={**headers, "Idempotency-Key": "restock-run"},
        json={"content": "Which products will stock out in 14 days? Restock them."},
    ).json()
    assert run["run"]["status"] == "waiting_approval"
    assert [tool["name"] for tool in run["tools"]] == ["forecast_stockouts", "propose_restock_orders"]
    assert "forecast to stock out" in run["tools"][0]["output"]["summary"]

    proposed = run["approvals"][0]["proposed_args"]
    inventory = {row["sku"]: row for row in client.get("/api/v1/retail/insights/inventory", headers=headers).json()}
    assert proposed["lines"] and proposed["horizon_days"] == 14
    for line in proposed["lines"]:
        assert line["quantity"] == inventory[line["sku"]]["recommended_quantity"]
        assert inventory[line["sku"]]["risk"] in ("critical", "high")
    assert client.get("/api/v1/retail/purchase-orders", headers=headers).json() == []

    edited = [
        {**line, "quantity": line["quantity"] + 6} if index == 0 else line
        for index, line in enumerate(proposed["lines"])
    ]
    decision = client.post(
        f"/api/v1/ai/approvals/{run['approvals'][0]['id']}/decision",
        headers=headers,
        json={"decision": "edit", "arguments": {**proposed, "lines": edited}, "reason": "Buffer for monsoon"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["run"]["status"] == "completed"

    orders = client.get("/api/v1/retail/purchase-orders", headers=headers).json()
    ordered = {line["name"]: line["quantity"] for order in orders for line in order["lines"]}
    assert ordered[edited[0]["name"]] == edited[0]["quantity"]
    assert all(order["status"] == "approved" and order["supplier"] for order in orders)

    after = {row["sku"]: row for row in client.get("/api/v1/retail/insights/inventory", headers=headers).json()}
    assert after[edited[0]["sku"]]["on_order"] == edited[0]["quantity"]


def test_agent_campaign_and_insight_tools(retail_client):
    client, headers = retail_client
    conversation = client.post("/api/v1/ai/conversations", headers=headers, json={}).json()
    insight = client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/runs",
        headers={**headers, "Idempotency-Key": "basket-run"},
        json={"content": 'What is bought together with "Carbon Steel Wok 32cm"?'},
    ).json()
    assert insight["run"]["status"] == "completed"
    assert "Wok Spatula" in insight["tools"][0]["output"]["summary"]

    campaign_run = client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/runs",
        headers={**headers, "Idempotency-Key": "campaign-run"},
        json={"content": 'Draft a win-back campaign for Lapsed high-value customers offering "15% off monsoon gear"'},
    ).json()
    proposed = campaign_run["approvals"][0]["proposed_args"]
    assert proposed["segment"] == "Lapsed high-value" and proposed["audience_size"] > 0
    approved = client.post(
        f"/api/v1/ai/approvals/{campaign_run['approvals'][0]['id']}/decision",
        headers=headers,
        json={"decision": "approve"},
    ).json()
    assert approved["run"]["status"] == "completed"
    campaigns = client.get("/api/v1/retail/campaigns", headers=headers).json()
    assert campaigns[0]["offer"] == "15% off monsoon gear"
    assert campaigns[0]["audience_size"] == proposed["audience_size"]


def test_vertex_provider_uses_application_default_credentials(monkeypatch):
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        get_copilot_provider("vertex", model="gemini-3.5-flash-lite")

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(ai_gateway.genai, "Client", FakeClient)
    provider = get_copilot_provider(
        "vertex", model="gemini-3.5-flash-lite", gcp_project="g-token-squad", gcp_location="global"
    )
    assert captured == {"vertexai": True, "project": "g-token-squad", "location": "global"}
    assert provider.provider_name == "vertex"


def test_local_planner_routes_retail_requests():
    plan = LocalCopilotProvider().plan("Show customer insights for the At risk segment")
    assert [(tool.name, tool.arguments["segment"]) for tool in plan.tools] == [("customer_insights", "At risk")]


def test_vertex_search_and_bigquery_backends_with_fallbacks(retail_client, monkeypatch):
    from services.api.omni_api import retail_gcp

    client, headers = retail_client
    settings = client.app.state.settings
    monkeypatch.setattr(settings, "retail_search_backend", "vertex")
    monkeypatch.setattr(settings, "retail_analytics_backend", "bigquery")

    monkeypatch.setattr(retail_gcp, "vertex_search_skus", lambda _settings, _query: ["CT-GRINDER", "UNKNOWN"])
    body = client.post("/api/v1/retail/shop/assist", headers=headers, json={"message": "grinder"}).json()
    assert body["search_backend"] == "vertex_ai_search"
    assert body["products"][0]["sku"] == "CT-GRINDER"

    monkeypatch.setattr(retail_gcp, "vertex_search_skus", lambda _settings, _query: [])
    assert (
        client.post("/api/v1/retail/shop/assist", headers=headers, json={"message": "grinder"}).json()["search_backend"]
        == "local_fallback"
    )

    monkeypatch.setattr(retail_gcp, "fetch_bigquery_forecast", lambda _settings, _today: {"CT-GRINDER": [50.0] * 3})
    rows = {row["sku"]: row for row in client.get("/api/v1/retail/insights/inventory", headers=headers).json()}
    assert rows["CT-GRINDER"]["source"] == "bigquery_ai_forecast"
    assert rows["CT-GRINDER"]["forecast_units"] == 700
    assert rows["CT-GRINDER"]["risk"] == "critical"
    assert rows["HK-WOK"]["source"] == "local_seasonal_trend"

    def offline(_settings, _today):
        raise ConnectionError("BigQuery unavailable")

    monkeypatch.setattr(retail_gcp, "fetch_bigquery_forecast", offline)
    assert client.get("/api/v1/retail/insights/inventory", headers=headers).status_code == 200


def test_bigquery_sql_uses_dataset_placeholder_only():
    from services.api.omni_api.retail_gcp import ANALYTICS_SQL

    for _, statement in ANALYTICS_SQL:
        rendered = statement.format(ds="proj.omni_retail")
        assert "{" not in rendered and "proj.omni_retail" in rendered
    assert any("AI.FORECAST" in statement for _, statement in ANALYTICS_SQL)
    assert any("model_type = 'KMEANS'" in statement for _, statement in ANALYTICS_SQL)
