"""Google Cloud integrations for retail: Vertex AI Search and BigQuery.

Request-time functions (search, forecast reads) are synchronous client calls
that callers run in a worker thread. Sync functions are used by the
`omni-retail-sync` job, never on the request path.
"""

import json
import logging
import threading
import time
from datetime import date
from functools import lru_cache
from typing import Any

from services.api.omni_api.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vertex AI Search
# ---------------------------------------------------------------------------

PRODUCT_SCHEMA = {
    "$schema": "https://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "title": {"type": "string", "keyPropertyMapping": "title", "retrievable": True},
        "description": {"type": "string", "keyPropertyMapping": "description", "retrievable": True},
        "sku": {"type": "string", "retrievable": True, "searchable": True},
        "brand": {"type": "string", "retrievable": True, "searchable": True, "indexable": True},
        "category": {"type": "string", "retrievable": True, "searchable": True, "indexable": True},
        "tags": {
            "type": "array",
            "items": {"type": "string", "retrievable": True, "searchable": True, "indexable": True},
        },
        "price": {"type": "number", "retrievable": True, "indexable": True},
        "in_stock": {"type": "boolean", "retrievable": True, "indexable": True},
    },
}


def _discovery_options(location: str):
    from google.api_core.client_options import ClientOptions

    if location == "global":
        return None
    return ClientOptions(api_endpoint=f"{location}-discoveryengine.googleapis.com")


@lru_cache(maxsize=4)
def _search_client(location: str):
    from google.cloud import discoveryengine_v1 as discoveryengine

    return discoveryengine.SearchServiceClient(client_options=_discovery_options(location))


def vertex_search_skus(settings: Settings, query: str, page_size: int = 20) -> list[str]:
    """Return SKUs ranked by Vertex AI Search for a shopper query."""
    from google.cloud import discoveryengine_v1 as discoveryengine

    if not settings.gcp_project or not settings.vertex_search_engine_id:
        raise RuntimeError("Vertex AI Search requires GOOGLE_CLOUD_PROJECT and OMNI_VERTEX_SEARCH_ENGINE_ID")
    location = settings.vertex_search_location
    serving_config = (
        f"projects/{settings.gcp_project}/locations/{location}/collections/default_collection/"
        f"engines/{settings.vertex_search_engine_id}/servingConfigs/default_search"
    )
    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=page_size,
        query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
            condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO
        ),
        spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
            mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO
        ),
    )
    response = _search_client(location).search(request, timeout=8)
    # The pager proxies the first page; iterating it would fetch further pages.
    return [result.document.id for result in response.results]


def product_document(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": product["sku"],
        "structData": {
            "title": product["name"],
            "description": product["description"],
            "sku": product["sku"],
            "brand": product["brand"],
            "category": product["category"],
            "tags": product["tags"],
            "price": product["price_cents"] / 100,
            "in_stock": product["on_hand"] > 0,
        },
    }


def sync_vertex_search_catalog(settings: Settings, products: list[dict[str, Any]]) -> dict[str, str]:
    """Create the data store and search app if needed, then fully reconcile the catalog."""
    from google.api_core.exceptions import AlreadyExists, NotFound
    from google.cloud import discoveryengine_v1 as discoveryengine

    if not settings.gcp_project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required")
    location = settings.vertex_search_location
    options = _discovery_options(location)
    collection = f"projects/{settings.gcp_project}/locations/{location}/collections/default_collection"
    data_store_id = settings.vertex_search_data_store_id
    engine_id = settings.vertex_search_engine_id or "harbourline-search"
    data_store_name = f"{collection}/dataStores/{data_store_id}"

    stores = discoveryengine.DataStoreServiceClient(client_options=options)
    try:
        stores.get_data_store(name=data_store_name)
    except NotFound:
        logger.info("Creating Vertex AI Search data store %s", data_store_id)
        stores.create_data_store(
            parent=collection,
            data_store_id=data_store_id,
            data_store=discoveryengine.DataStore(
                display_name="Harbourline products",
                industry_vertical=discoveryengine.IndustryVertical.GENERIC,
                solution_types=[discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH],
                content_config=discoveryengine.DataStore.ContentConfig.NO_CONTENT,
            ),
        ).result(timeout=900)

    try:
        discoveryengine.SchemaServiceClient(client_options=options).update_schema(
            request=discoveryengine.UpdateSchemaRequest(
                schema=discoveryengine.Schema(
                    name=f"{data_store_name}/schemas/default_schema", json_schema=json.dumps(PRODUCT_SCHEMA)
                ),
                allow_missing=True,
            )
        ).result(timeout=900)
    except Exception as exc:  # Auto-detected schemas still serve search; the explicit one improves ranking.
        logger.warning("Vertex AI Search schema update skipped: %s", exc)

    documents = [product_document(product) for product in products]
    import_request: dict[str, Any] = {
        "parent": f"{data_store_name}/branches/default_branch",
        "reconciliation_mode": discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL,
    }
    source = "inline"
    if settings.gcs_bucket:
        from google.cloud import storage

        uri = f"gs://{settings.gcs_bucket}/catalog/products.jsonl"
        blob = storage.Client(project=settings.gcp_project).bucket(settings.gcs_bucket).blob("catalog/products.jsonl")
        blob.upload_from_string("\n".join(json.dumps(item) for item in documents), content_type="application/json")
        import_request["gcs_source"] = discoveryengine.GcsSource(input_uris=[uri], data_schema="document")
        source = uri
    else:
        import_request["inline_source"] = discoveryengine.ImportDocumentsRequest.InlineSource(
            documents=[
                discoveryengine.Document(id=item["id"], json_data=json.dumps(item["structData"])) for item in documents
            ]
        )
    discoveryengine.DocumentServiceClient(client_options=options).import_documents(
        request=discoveryengine.ImportDocumentsRequest(**import_request)
    ).result(timeout=1800)

    engines = discoveryengine.EngineServiceClient(client_options=options)
    try:
        engines.get_engine(name=f"{collection}/engines/{engine_id}")
    except NotFound:
        logger.info("Creating Vertex AI Search app %s", engine_id)
        try:
            engines.create_engine(
                parent=collection,
                engine_id=engine_id,
                engine=discoveryengine.Engine(
                    display_name="Harbourline product search",
                    solution_type=discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH,
                    industry_vertical=discoveryengine.IndustryVertical.GENERIC,
                    data_store_ids=[data_store_id],
                    search_engine_config=discoveryengine.Engine.SearchEngineConfig(
                        search_tier=discoveryengine.SearchTier.SEARCH_TIER_STANDARD
                    ),
                ),
            ).result(timeout=900)
        except AlreadyExists:
            pass
    return {"data_store_id": data_store_id, "engine_id": engine_id, "documents": str(len(documents)), "source": source}


# ---------------------------------------------------------------------------
# BigQuery analytics
# ---------------------------------------------------------------------------

TABLE_SCHEMAS: dict[str, list[tuple[str, str, str]]] = {
    "products": [
        ("product_id", "STRING", "REQUIRED"),
        ("sku", "STRING", "REQUIRED"),
        ("name", "STRING", "REQUIRED"),
        ("brand", "STRING", "NULLABLE"),
        ("category", "STRING", "REQUIRED"),
        ("price", "FLOAT64", "REQUIRED"),
        ("unit_cost", "FLOAT64", "REQUIRED"),
    ],
    "inventory": [
        ("sku", "STRING", "REQUIRED"),
        ("on_hand", "INT64", "REQUIRED"),
        ("on_order", "INT64", "REQUIRED"),
        ("safety_stock", "INT64", "REQUIRED"),
        ("lead_time_days", "INT64", "REQUIRED"),
        ("case_pack", "INT64", "REQUIRED"),
        ("supplier", "STRING", "NULLABLE"),
    ],
    "orders": [
        ("order_id", "STRING", "REQUIRED"),
        ("number", "STRING", "REQUIRED"),
        ("customer_id", "STRING", "NULLABLE"),
        ("placed_at", "TIMESTAMP", "REQUIRED"),
        ("order_date", "DATE", "REQUIRED"),
        ("channel", "STRING", "REQUIRED"),
        ("store", "STRING", "REQUIRED"),
        ("subtotal", "FLOAT64", "REQUIRED"),
        ("discount", "FLOAT64", "REQUIRED"),
        ("total", "FLOAT64", "REQUIRED"),
    ],
    "order_lines": [
        ("order_id", "STRING", "REQUIRED"),
        ("order_date", "DATE", "REQUIRED"),
        ("sku", "STRING", "REQUIRED"),
        ("quantity", "INT64", "REQUIRED"),
        ("unit_price", "FLOAT64", "REQUIRED"),
        ("line_total", "FLOAT64", "REQUIRED"),
    ],
}

ANALYTICS_SQL: tuple[tuple[str, str], ...] = (
    (
        "daily_demand",
        """
CREATE OR REPLACE TABLE `{ds}.daily_demand` AS
WITH days AS (
  SELECT day FROM UNNEST(GENERATE_DATE_ARRAY(
    DATE_SUB(CURRENT_DATE('Asia/Singapore'), INTERVAL 180 DAY),
    DATE_SUB(CURRENT_DATE('Asia/Singapore'), INTERVAL 1 DAY))) AS day
), sales AS (
  SELECT sku, order_date AS day, SUM(quantity) AS units FROM `{ds}.order_lines` GROUP BY sku, day
)
SELECT p.sku, p.name, p.category, d.day, TIMESTAMP(d.day) AS day_ts, COALESCE(s.units, 0) AS units
FROM `{ds}.products` AS p
CROSS JOIN days AS d
LEFT JOIN sales AS s ON s.sku = p.sku AND s.day = d.day
""",
    ),
    (
        "demand_forecast (BigQuery AI.FORECAST / TimesFM)",
        """
CREATE OR REPLACE TABLE `{ds}.demand_forecast` AS
SELECT *
FROM AI.FORECAST(
  (SELECT sku, day_ts, units FROM `{ds}.daily_demand`),
  data_col => 'units',
  timestamp_col => 'day_ts',
  id_cols => ['sku'],
  horizon => 60,
  confidence_level => 0.9
)
""",
    ),
    (
        "customer_rfm",
        """
CREATE OR REPLACE TABLE `{ds}.customer_rfm` AS
WITH base AS (
  SELECT
    customer_id,
    DATE_DIFF(CURRENT_DATE('Asia/Singapore'), MAX(order_date), DAY) AS recency_days,
    DATE_DIFF(CURRENT_DATE('Asia/Singapore'), MIN(order_date), DAY) AS tenure_days,
    COUNT(*) AS frequency,
    SUM(total) AS monetary
  FROM `{ds}.orders`
  WHERE customer_id IS NOT NULL
    AND order_date >= DATE_SUB(CURRENT_DATE('Asia/Singapore'), INTERVAL 180 DAY)
  GROUP BY customer_id
), thresholds AS (
  SELECT APPROX_QUANTILES(monetary, 10)[OFFSET(7)] AS high_value FROM base
)
SELECT
  b.*,
  CASE
    WHEN recency_days > 75 AND monetary >= t.high_value THEN 'Lapsed high-value'
    WHEN recency_days <= 30 AND frequency >= 8 AND monetary >= t.high_value THEN 'Champions'
    WHEN recency_days <= 60 AND frequency >= 5 THEN 'Loyal'
    WHEN frequency = 1 AND tenure_days <= 45 THEN 'New'
    WHEN recency_days <= 45 AND frequency >= 2 THEN 'Promising'
    WHEN recency_days <= 120 AND frequency >= 2 THEN 'At risk'
    ELSE 'Hibernating'
  END AS segment
FROM base AS b
CROSS JOIN thresholds AS t
""",
    ),
    (
        "customer_clusters (BigQuery ML k-means)",
        """
CREATE OR REPLACE MODEL `{ds}.customer_clusters`
OPTIONS (model_type = 'KMEANS', num_clusters = 5, standardize_features = TRUE) AS
SELECT recency_days, frequency, monetary FROM `{ds}.customer_rfm`
""",
    ),
    (
        "customer_segments",
        """
CREATE OR REPLACE TABLE `{ds}.customer_segments` AS
SELECT customer_id, recency_days, frequency, monetary, segment, centroid_id AS ml_cluster
FROM ML.PREDICT(MODEL `{ds}.customer_clusters`, (SELECT * FROM `{ds}.customer_rfm`))
""",
    ),
    (
        "basket_pairs",
        """
CREATE OR REPLACE TABLE `{ds}.basket_pairs` AS
WITH baskets AS (
  SELECT DISTINCT order_id, sku FROM `{ds}.order_lines`
  WHERE order_date >= DATE_SUB(CURRENT_DATE('Asia/Singapore'), INTERVAL 180 DAY)
), totals AS (
  SELECT COUNT(DISTINCT order_id) AS orders FROM baskets
), items AS (
  SELECT sku, COUNT(*) AS orders FROM baskets GROUP BY sku
), pairs AS (
  SELECT a.sku AS anchor_sku, b.sku AS companion_sku, COUNT(*) AS orders_together
  FROM baskets AS a
  JOIN baskets AS b ON a.order_id = b.order_id AND a.sku != b.sku
  GROUP BY anchor_sku, companion_sku
)
SELECT
  p.anchor_sku, pa.name AS anchor_name, p.companion_sku, pc.name AS companion_name, p.orders_together,
  p.orders_together / t.orders AS support,
  p.orders_together / ia.orders AS confidence,
  (p.orders_together / ia.orders) / (ic.orders / t.orders) AS lift
FROM pairs AS p
CROSS JOIN totals AS t
JOIN items AS ia ON ia.sku = p.anchor_sku
JOIN items AS ic ON ic.sku = p.companion_sku
JOIN `{ds}.products` AS pa ON pa.sku = p.anchor_sku
JOIN `{ds}.products` AS pc ON pc.sku = p.companion_sku
WHERE p.orders_together >= 5
""",
    ),
    (
        "v_daily_sales",
        """
CREATE OR REPLACE VIEW `{ds}.v_daily_sales` AS
SELECT order_date, channel, store,
  COUNT(*) AS orders, SUM(total) AS revenue, SUM(discount) AS discounts,
  COUNT(DISTINCT customer_id) AS customers
FROM `{ds}.orders`
GROUP BY order_date, channel, store
""",
    ),
    (
        "v_category_sales",
        """
CREATE OR REPLACE VIEW `{ds}.v_category_sales` AS
SELECT l.order_date, p.category, p.name AS product, l.sku,
  SUM(l.quantity) AS units, SUM(l.line_total) AS revenue,
  SUM(l.line_total - l.quantity * p.unit_cost) AS gross_margin
FROM `{ds}.order_lines` AS l
JOIN `{ds}.products` AS p USING (sku)
GROUP BY l.order_date, p.category, p.name, l.sku
""",
    ),
    (
        "v_inventory_risk",
        """
CREATE OR REPLACE VIEW `{ds}.v_inventory_risk` AS
WITH f AS (
  SELECT sku, SUM(forecast_value) AS forecast_14d
  FROM `{ds}.demand_forecast`
  WHERE DATE(forecast_timestamp) BETWEEN CURRENT_DATE('Asia/Singapore')
    AND DATE_ADD(CURRENT_DATE('Asia/Singapore'), INTERVAL 13 DAY)
  GROUP BY sku
)
SELECT p.sku, p.name, p.category, i.supplier, i.on_hand, i.on_order, i.lead_time_days,
  ROUND(f.forecast_14d, 1) AS forecast_14d,
  ROUND(SAFE_DIVIDE(i.on_hand, f.forecast_14d / 14), 1) AS days_of_cover,
  CASE
    WHEN SAFE_DIVIDE(i.on_hand, f.forecast_14d / 14) < i.lead_time_days THEN 'critical'
    WHEN SAFE_DIVIDE(i.on_hand, f.forecast_14d / 14) < i.lead_time_days + 14 THEN 'high'
    ELSE 'ok'
  END AS risk
FROM `{ds}.products` AS p
JOIN `{ds}.inventory` AS i USING (sku)
LEFT JOIN f USING (sku)
""",
    ),
    (
        "v_forecast_vs_actual",
        """
CREATE OR REPLACE VIEW `{ds}.v_forecast_vs_actual` AS
SELECT d.sku, d.name, d.category, d.day AS date, d.units AS units, 'actual' AS series,
  CAST(NULL AS FLOAT64) AS lower_bound, CAST(NULL AS FLOAT64) AS upper_bound
FROM `{ds}.daily_demand` AS d
UNION ALL
SELECT f.sku, p.name, p.category, DATE(f.forecast_timestamp), f.forecast_value, 'forecast',
  f.prediction_interval_lower_bound, f.prediction_interval_upper_bound
FROM `{ds}.demand_forecast` AS f
JOIN `{ds}.products` AS p USING (sku)
""",
    ),
    (
        "v_segment_summary",
        """
CREATE OR REPLACE VIEW `{ds}.v_segment_summary` AS
SELECT segment, ml_cluster, COUNT(*) AS customers, SUM(monetary) AS revenue,
  AVG(frequency) AS avg_orders, AVG(recency_days) AS avg_recency_days
FROM `{ds}.customer_segments`
GROUP BY segment, ml_cluster
""",
    ),
)


def _bigquery_client(settings: Settings):
    from google.cloud import bigquery

    if not settings.gcp_project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for BigQuery")
    return bigquery.Client(project=settings.gcp_project, location=settings.bigquery_location)


def sync_bigquery(settings: Settings, tables: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Load operational tables, then build forecasts, segments, basket rules, and Looker views."""
    from google.cloud import bigquery

    client = _bigquery_client(settings)
    dataset_id = f"{settings.gcp_project}.{settings.bigquery_dataset}"
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = settings.bigquery_location
    client.create_dataset(dataset, exists_ok=True)
    completed = []
    for name, rows in tables.items():
        schema = [bigquery.SchemaField(field, kind, mode=mode) for field, kind, mode in TABLE_SCHEMAS[name]]
        client.load_table_from_json(
            rows,
            f"{dataset_id}.{name}",
            job_config=bigquery.LoadJobConfig(
                schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
            ),
        ).result()
        completed.append(f"loaded {name} ({len(rows)} rows)")
    for label, statement in ANALYTICS_SQL:
        client.query(statement.format(ds=dataset_id)).result()
        completed.append(f"built {label}")
    _forecast_cache.clear()
    return completed


_forecast_cache: dict[tuple[str, str, str], tuple[float, dict[str, list[float]]]] = {}
_forecast_lock = threading.Lock()


def fetch_bigquery_forecast(settings: Settings, today: date, ttl_seconds: int = 600) -> dict[str, list[float]]:
    """Daily AI.FORECAST values per SKU from today onward, cached briefly per process."""
    from google.cloud import bigquery

    key = (settings.gcp_project or "", settings.bigquery_dataset, today.isoformat())
    with _forecast_lock:
        cached = _forecast_cache.get(key)
        if cached and time.monotonic() - cached[0] < ttl_seconds:
            return cached[1]
    client = _bigquery_client(settings)
    query = (
        f"SELECT sku, DATE(forecast_timestamp) AS day, forecast_value "
        f"FROM `{settings.gcp_project}.{settings.bigquery_dataset}.demand_forecast` "
        f"WHERE DATE(forecast_timestamp) >= @today ORDER BY sku, day"
    )
    rows = client.query(
        query,
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("today", "DATE", today)]),
        timeout=10,
    ).result(timeout=10)
    forecasts: dict[str, list[float]] = {}
    for row in rows:
        forecasts.setdefault(row["sku"], []).append(float(row["forecast_value"]))
    with _forecast_lock:
        _forecast_cache[key] = (time.monotonic(), forecasts)
    return forecasts
