# Omni Commerce on Google Cloud

Omni Commerce is the retail edition of Omni Model. It was built for the Google
Cloud AI Builder Cup 2026, Retail & Commerce track, by team G-token Squad.
Harbourline is a fictional Singapore lifestyle retailer whose demo data has 56
products, 1,200 loyalty members, two stores plus online, and about 9,700 orders
over 180 days.

## What runs where

```
 Shopper / merchant ──► omni-web (Cloud Run, nginx)
                           │  same-origin proxy: /api, /health
                           ▼
                        omni-api (Cloud Run, FastAPI) ──► Cloud SQL for PostgreSQL
                           │                               (orders, catalogue, approvals, audit)
          ┌────────────────┼────────────────────────┐
          ▼                ▼                        ▼
 Gemini on Vertex AI   Vertex AI Search      BigQuery (AI.FORECAST demand_forecast)
 · shopper intent      · product retrieval        ▲
 · grounded replies                               │
 · Omni Agent plans                                │
                                                   │
 omni-retail-sync (Cloud Run job, nightly via Cloud Scheduler)
   Postgres ─► Cloud Storage (catalog JSONL) ─► Vertex AI Search import
   Postgres ─► BigQuery tables ─► AI.FORECAST (TimesFM), BigQuery ML k-means,
                                   basket rules, Looker views ─► Looker Studio
```

| Capability | Google Cloud service | Where in the code |
|---|---|---|
| Conversational shopping | Gemini on Vertex AI | `services/api/omni_api/retail_shop.py` |
| Product discovery | Vertex AI Search | `retail_gcp.vertex_search_skus` / `sync_vertex_search_catalog` |
| Demand forecasting | BigQuery `AI.FORECAST` (TimesFM) | `retail_gcp.ANALYTICS_SQL`, `retail_analytics.forecast_with_backend` |
| Segmentation | RFM rules + BigQuery ML `KMEANS` | `retail_analytics.customer_rfm`, `customer_clusters` model |
| Basket analysis | Association rules in SQL and Python | `basket_pairs` table, `retail_analytics.basket_rules` |
| Human-approved actions | Omni Agent with an approval gate | `retail_tools.py` (`propose_restock_orders`, `draft_campaign`) |
| Commerce app and APIs | Cloud Run | `deploy/gcp/` |
| Operational data | Cloud SQL for PostgreSQL | `models.py`, migration `0009_retail_commerce` |
| Product assets | Cloud Storage | catalogue JSONL for Vertex AI Search import |
| Business insights | Looker Studio on BigQuery views | `v_*` views |

Every Google Cloud dependency has a local fallback. The fallbacks are the
seasonal forecast model, the keyword ranker and template replies. If one Google
Cloud service is slow or not yet indexed, the demo still works. The UI shows
which backend served each answer.

## Deploy

Prerequisites: install the `gcloud` CLI and have a project with billing enabled.

```bash
brew install --cask google-cloud-sdk        # or see cloud.google.com/sdk
gcloud auth login
PROJECT_ID=<your-project-id> deploy/gcp/deploy.sh
```

The first run takes about 20–25 minutes, mostly Cloud SQL creation and the first
Vertex AI Search import. The script is idempotent: re-running it builds new
images and redeploys. Use `SKIP_SYNC=true` to skip the data refresh.

| Variable | Default | Notes |
|---|---|---|
| `REGION` | `asia-southeast1` | Cloud Run, Cloud SQL, Artifact Registry |
| `AI_MODEL` / `AI_LOCATION` | `gemini-3.5-flash-lite` / `global` | Gemini on Vertex AI |
| `BQ_LOCATION` | `US` | BigQuery dataset location; the US multi-region supports AI.FORECAST and BigQuery ML |
| `SQL_TIER` | `db-g1-small` | Raise this for load testing |
| `LOOKER_EMBED_URL` | empty | Set it after building the dashboard (see below) |

If Vertex AI Search was never used in the project, open **Console → AI
Applications** once to accept its terms before running the script. Newly
imported documents can take several minutes to become searchable. Until then
the storefront uses the local ranker and shows "Local ranking (fallback)".

Both Cloud Run services run with `--min-instances 1`, so judges never hit a
cold start. Lower this after the event to save cost.

### Phoenix tracing

To send Cloud Run traces to your existing Phoenix (for example, the staging one
behind Caddy), set these when deploying:

```bash
PHOENIX_COLLECTOR_ENDPOINT=https://<traces-domain>/v1/traces \
PHOENIX_API_KEY=<PHOENIX_ADMIN_SECRET or a Phoenix system key> \
PROJECT_ID=<id> SKIP_SYNC=true deploy/gcp/deploy.sh
```

The API key is stored in Secret Manager. Traces land in the `omni-commerce`
project (override with `PHOENIX_PROJECT`). Prompts and replies are hidden by
default; set `TRACE_CONTENT=true` to capture them for debugging. Each request
produces one trace:

- **Storefront turn:** `shop.assist` (one Phoenix session per chat) contains
  two Gemini `GenerateContent` spans (intent, reply) and a `retail.search`
  retriever span. The retriever span records whether Vertex AI Search or the
  fallback served the results.
- **Agent run:** `copilot.agent.run` contains the Gemini planning span, one
  `tool.*` span per tool, and `retail.forecast`. The forecast span records
  whether BigQuery AI.FORECAST or the local model was used.

### Security posture for the demo

- Sign-in uses development auth (`OMNI_ALLOW_DEV_AUTH=true`) so judges can
  enter as `owner@omni.example`. Turn it off and front the app with
  Identity-Aware Proxy or a real IdP before handling real customers.
- The database URL and JWT secret live in Secret Manager. The runtime service
  account has only the roles listed in `deploy.sh`.
- Shopper text is untrusted. Model prompts redact emails and phone numbers,
  replies may only cite retrieved SKUs, and write actions always need human
  approval.

## Build the Looker Studio dashboard (about 10 minutes)

`deploy.sh` prints a Looker Studio link that creates a report with every
BigQuery view already connected. Build these pages:

1. **Sales pulse:** scorecards for revenue, orders and customers from
   `v_daily_sales`, a time series of revenue by `order_date`, and a breakdown by
   `channel`.
2. **Demand and stock:** a table from `v_inventory_risk` sorted by
   `days_of_cover` with conditional formatting on `risk`, plus a time series from
   `v_forecast_vs_actual` (dimension `date`, breakdown `series`, filter
   `name`).
3. **Customers:** a bar chart of `customers` and `revenue` by `segment` from
   `v_segment_summary`, plus a pivot of `segment` × `ml_cluster`.
4. **Baskets:** a table from `basket_pairs` sorted by `lift`.

Next, open **Share → Anyone with the link can view**, then **File → Embed
report**, and copy the URL. Redeploy with
`LOOKER_EMBED_URL='<url>' SKIP_SYNC=true`. The dashboard then appears at the
bottom of Merchant insights.

## Run locally

```bash
make infra-up migrate retail-seed     # Postgres + Harbourline data
make api                              # http://localhost:8001/docs
make web-retail                       # http://localhost:5173
```

Set `OMNI_AI_PROVIDER=gemini` with a `GEMINI_API_KEY` for live AI, or
`OMNI_AI_PROVIDER=vertex` with `GOOGLE_CLOUD_PROJECT` after running
`gcloud auth application-default login`. With the default `local` provider,
everything runs offline and deterministically.
