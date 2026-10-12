#!/usr/bin/env bash
# Deploy Omni Commerce (retail edition) to Google Cloud.
#
#   PROJECT_ID=my-project deploy/gcp/deploy.sh
#
# Creates (idempotently): Artifact Registry, a runtime service account, Cloud SQL
# for PostgreSQL, a Cloud Storage bucket, secrets, the API and web Cloud Run
# services, migration/seed and analytics-sync Cloud Run jobs, and a nightly
# scheduler. Re-running redeploys new images and refreshes data.
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project ID}"
REGION="${REGION:-asia-southeast1}"
AI_LOCATION="${AI_LOCATION:-global}"
AI_MODEL="${AI_MODEL:-gemini-3.5-flash-lite}"
REPO="${REPO:-omni}"
SQL_INSTANCE="${SQL_INSTANCE:-omni-pg}"
SQL_TIER="${SQL_TIER:-db-g1-small}"
BUCKET="${BUCKET:-${PROJECT_ID}-omni-retail}"
BQ_DATASET="${BQ_DATASET:-omni_retail}"
BQ_LOCATION="${BQ_LOCATION:-US}"
SEARCH_ENGINE_ID="${SEARCH_ENGINE_ID:-harbourline-search}"
LOOKER_EMBED_URL="${LOOKER_EMBED_URL:-}"
SKIP_SYNC="${SKIP_SYNC:-false}"
SA_NAME="omni-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/omni-api:${TAG}"
WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/omni-web:${TAG}"

step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

cd "$(dirname "$0")/../.."
gcloud config set project "$PROJECT_ID" >/dev/null

step "Enabling Google Cloud APIs"
gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com aiplatform.googleapis.com \
  discoveryengine.googleapis.com bigquery.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
  cloudscheduler.googleapis.com iam.googleapis.com

step "Artifact Registry repository"
exists gcloud artifacts repositories describe "$REPO" --location "$REGION" ||
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"

step "Runtime service account and least-privilege roles"
exists gcloud iam service-accounts describe "$SA" ||
  gcloud iam service-accounts create "$SA_NAME" --display-name "Omni Commerce Cloud Run"
for role in roles/aiplatform.user roles/discoveryengine.editor roles/bigquery.dataEditor \
  roles/bigquery.jobUser roles/cloudsql.client roles/secretmanager.secretAccessor \
  roles/storage.objectAdmin roles/run.invoker; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" \
    --role "$role" --condition=None >/dev/null
done

step "Cloud Storage bucket for catalog assets"
exists gcloud storage buckets describe "gs://${BUCKET}" ||
  gcloud storage buckets create "gs://${BUCKET}" --location "$REGION" --uniform-bucket-level-access

step "Cloud SQL for PostgreSQL (the first run takes ~10 minutes)"
if ! exists gcloud sql instances describe "$SQL_INSTANCE"; then
  gcloud sql instances create "$SQL_INSTANCE" --database-version POSTGRES_17 --edition ENTERPRISE \
    --tier "$SQL_TIER" --region "$REGION" --storage-size 10 --storage-auto-increase
fi
CONNECTION="$(gcloud sql instances describe "$SQL_INSTANCE" --format 'value(connectionName)')"
exists gcloud sql databases describe omni --instance "$SQL_INSTANCE" ||
  gcloud sql databases create omni --instance "$SQL_INSTANCE"

step "Secrets"
if ! exists gcloud secrets describe omni-database-url; then
  DB_PASSWORD="$(openssl rand -hex 24)"
  if exists gcloud sql users describe omni --instance "$SQL_INSTANCE"; then
    gcloud sql users set-password omni --instance "$SQL_INSTANCE" --password "$DB_PASSWORD"
  else
    gcloud sql users create omni --instance "$SQL_INSTANCE" --password "$DB_PASSWORD"
  fi
  printf 'postgresql+asyncpg://omni:%s@/omni?host=/cloudsql/%s' "$DB_PASSWORD" "$CONNECTION" |
    gcloud secrets create omni-database-url --data-file=- --replication-policy automatic
fi
exists gcloud secrets describe omni-jwt-secret ||
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets create omni-jwt-secret --data-file=- --replication-policy automatic

step "Building images with Cloud Build"
gcloud builds submit --config deploy/gcp/cloudbuild.yaml \
  --substitutions "_API_IMAGE=${API_IMAGE},_WEB_IMAGE=${WEB_IMAGE}" .

# "^|^" switches gcloud's list delimiter so values may contain commas.
COMMON_ENV="^|^OMNI_ENVIRONMENT=hackathon|OMNI_AI_PROVIDER=vertex|OMNI_AI_MODEL=${AI_MODEL}"
COMMON_ENV+="|GOOGLE_CLOUD_PROJECT=${PROJECT_ID}|GOOGLE_CLOUD_LOCATION=${AI_LOCATION}"
COMMON_ENV+="|OMNI_GEMINI_STT_MODEL=${AI_MODEL}|OMNI_AI_TIMEOUT_SECONDS=30|OMNI_ALLOW_DEV_AUTH=true"
COMMON_ENV+="|OMNI_STORAGE_BACKEND=local|OMNI_ATTACHMENT_DIR=/tmp/attachments|OMNI_TRACING_ENABLED=false"
COMMON_ENV+="|OMNI_GCS_BUCKET=${BUCKET}|OMNI_BIGQUERY_DATASET=${BQ_DATASET}|OMNI_BIGQUERY_LOCATION=${BQ_LOCATION}"
COMMON_ENV+="|OMNI_VERTEX_SEARCH_ENGINE_ID=${SEARCH_ENGINE_ID}"
SECRETS="OMNI_DATABASE_URL=omni-database-url:latest,OMNI_JWT_SECRET=omni-jwt-secret:latest"

step "Migrating and seeding the database (Cloud Run job)"
gcloud run jobs deploy omni-migrate --image "$API_IMAGE" --region "$REGION" --service-account "$SA" \
  --set-cloudsql-instances "$CONNECTION" --set-env-vars "$COMMON_ENV" --set-secrets "$SECRETS" \
  --command sh --args=-c,"alembic upgrade head && python -m services.api.omni_api.seed && python -m services.api.omni_api.retail_seed" \
  --max-retries 0 --task-timeout 900
gcloud run jobs execute omni-migrate --region "$REGION" --wait

step "Retail sync job: Vertex AI Search catalog + BigQuery AI.FORECAST, segments, basket rules"
gcloud run jobs deploy omni-retail-sync --image "$API_IMAGE" --region "$REGION" --service-account "$SA" \
  --set-cloudsql-instances "$CONNECTION" --set-env-vars "$COMMON_ENV" --set-secrets "$SECRETS" \
  --command python --args=-m,services.api.omni_api.retail_sync,--all \
  --max-retries 1 --task-timeout 3600 --memory 1Gi
if [[ "$SKIP_SYNC" != "true" ]]; then
  gcloud run jobs execute omni-retail-sync --region "$REGION" --wait
fi

step "Deploying the API (Cloud Run)"
gcloud run deploy omni-api --image "$API_IMAGE" --region "$REGION" --service-account "$SA" \
  --add-cloudsql-instances "$CONNECTION" \
  --set-env-vars "${COMMON_ENV}|OMNI_RETAIL_SEARCH_BACKEND=vertex|OMNI_RETAIL_ANALYTICS_BACKEND=bigquery|OMNI_LOOKER_EMBED_URL=${LOOKER_EMBED_URL}" \
  --set-secrets "$SECRETS" --port 8001 --cpu 1 --memory 1Gi --timeout 300 \
  --min-instances 1 --max-instances 4 --allow-unauthenticated
API_URL="$(gcloud run services describe omni-api --region "$REGION" --format 'value(status.url)')"

step "Deploying the web app (Cloud Run)"
gcloud run deploy omni-web --image "$WEB_IMAGE" --region "$REGION" --service-account "$SA" \
  --set-env-vars "API_URL=${API_URL},API_HOST=${API_URL#https://}" \
  --port 8080 --cpu 1 --memory 256Mi --min-instances 1 --max-instances 4 --allow-unauthenticated
WEB_URL="$(gcloud run services describe omni-web --region "$REGION" --format 'value(status.url)')"

step "Nightly analytics refresh (03:00 Singapore)"
SCHEDULER_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/omni-retail-sync:run"
exists gcloud scheduler jobs describe omni-retail-sync-nightly --location "$REGION" ||
  gcloud scheduler jobs create http omni-retail-sync-nightly --location "$REGION" \
    --schedule "0 3 * * *" --time-zone "Asia/Singapore" --uri "$SCHEDULER_URI" --http-method POST \
    --oauth-service-account-email "$SA"

LOOKER_CREATE="https://lookerstudio.google.com/reporting/create?c.mode=edit&r.reportName=Harbourline%20Retail"
index=0
for view in v_daily_sales v_category_sales v_inventory_risk v_forecast_vs_actual v_segment_summary basket_pairs; do
  LOOKER_CREATE+="&ds.ds${index}.connector=bigQuery&ds.ds${index}.type=TABLE&ds.ds${index}.projectId=${PROJECT_ID}"
  LOOKER_CREATE+="&ds.ds${index}.datasetId=${BQ_DATASET}&ds.ds${index}.tableId=${view}&ds.ds${index}.datasourceName=${view}"
  index=$((index + 1))
done

cat <<EOF

Omni Commerce is live.
  Web app:  ${WEB_URL}   (sign in with owner@omni.example)
  API docs: ${API_URL}/docs

Next: build the Looker Studio dashboard from the BigQuery views:
  ${LOOKER_CREATE}
Then share it ("Anyone with the link can view"), copy File > Embed report URL and redeploy with
  LOOKER_EMBED_URL='<embed url>' PROJECT_ID=${PROJECT_ID} SKIP_SYNC=true deploy/gcp/deploy.sh
EOF
