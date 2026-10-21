# Omni Model

Omni Model is a multi-tenant business automation platform with an AI agent at
its core. It has a React web client, an Expo mobile client, a modular FastAPI
API, a background worker, a realtime voice gateway, and an MCP server. It grew
out of a multimodal AI moderation proof of concept, which is still in the repo.

Every tenant's data is isolated and every write leaves an append-only audit
event. Any AI action that changes data or reaches outside the system waits for
a human to approve it.

## Contents

- [What's inside](#whats-inside)
- [Web editions](#web-editions)
- [Quick start](#quick-start)
- [Feature guide](#feature-guide)
- [Configuration](#configuration)
- [Tests](#tests)
- [Development workflow](#development-workflow)
- [Deployment](#deployment)
- [Repository layout](#repository-layout)
- [Legacy moderation proof of concept](#legacy-moderation-proof-of-concept)

## What's inside

| Area | What it does | Design notes |
|---|---|---|
| Platform foundation | JWT auth, tenant isolation, generated API contracts, audit events | [ADR 0001](docs/architecture/0001-platform-skeleton.md) |
| Operations | Customers, jobs, scheduling, technicians, invoices, payments, offline read-only mode | [ADR 0002](docs/architecture/0002-operations-domain-commands.md), [ADR 0003](docs/architecture/0003-operations-mvp-storage-and-payments.md) |
| Omni Agent | Durable, tool-using AI runs that pause for human approval | [ADR 0004](docs/architecture/0004-ai-copilot-runtime.md) |
| Automations | Versioned workflow templates with shadow mode, approvals, retries, and a kill switch | [ADR 0005](docs/architecture/0005-automation-runtime.md) |
| Voice pilot | After-hours call intake with consent, emergency detection, and safe transfer | [ADR 0006](docs/architecture/0006-realtime-voice-pilot.md) |
| Call intelligence | Redacted, versioned transcripts and call facts with human review | [ADR 0007](docs/architecture/0007-call-data-intelligence.md) |
| Environmental | Lab workbook validation, screening, and cited report drafts, over MCP and on the web | [ADR 0009](docs/architecture/0009-environmental-mcp.md), [ADR 0010](docs/architecture/0010-environmental-workspace.md) |
| Omni Commerce | Retail edition: AI storefront, demand forecasts, segments, and approved restock/campaign actions | [Google Cloud guide](docs/retail/GOOGLE_CLOUD.md) |
| Scale readiness | Release gates, canary deploys, backups, and monitoring | [ADR 0008](docs/architecture/0008-scale-readiness.md) |

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the staged product and platform
roadmap.

## Web editions

One web app serves several product editions. Build-time variables choose the
edition:

| Variable | Effect |
|---|---|
| *(none)* | Environmental demo. Shows the operations, Assistant, and Environmental workspaces. |
| `VITE_PRODUCT_EDITION=retail` | **Omni Commerce**. Leads with the Shop, Catalog, and Insights workspaces for the `harbourline` tenant. |
| `VITE_ENABLE_ADVANCED_MODULES=true` | Adds the Automations, Voice, and Intelligence workspaces to the navigation. They are implemented in every edition but hidden by default. |
| `VITE_API_URL` | Sets the API base URL. Defaults to `http://localhost:8001`. |

## Quick start

### Requirements

- Python 3.12 or newer
- Node.js 22 or newer
- Docker with Compose, for PostgreSQL, Redis, and MinIO
- [`uv`](https://docs.astral.sh/uv/)
- A Gemini API key for live model calls. Without one, the built-in `local`
  provider runs deterministically.

On macOS, you can install and start a lightweight Homebrew-based Docker
runtime:

```bash
brew install docker docker-compose docker-buildx colima
colima start --cpu 2 --memory 4
```

If Docker doesn't find Homebrew's Compose plugin, add
`/opt/homebrew/lib/docker/cli-plugins` to `cliPluginsExtraDirs` in
`~/.docker/config.json`.

### Install and configure

```bash
make setup          # uv sync --locked --dev && npm ci
cp env.example .env
```

In `.env`, set `GEMINI_API_KEY` and choose a non-empty `USER_API_KEY`. To use
Gemini in the Omni Assistant, set `OMNI_AI_PROVIDER=gemini` and
`OMNI_AI_MODEL=gemini-3.5-flash-lite`. Leave `GOOGLE_GEMINI_BASE_URL` blank if
your key comes from AI Studio.

### Run

```bash
make dev            # starts infra, migrates, seeds, then runs API + worker + web
make mobile         # Expo app, in a second terminal
```

To run the Omni Commerce retail edition with its Harbourline demo data:

```bash
make dev-retail     # starts infra, migrates, loads retail seed data, runs API + worker + retail web
```

You can also run the API and worker entirely in containers. Migrations and
idempotent seed data run automatically:

```bash
make platform-up
```

| Service | URL |
|---|---|
| React web | <http://localhost:5173> |
| Platform API (OpenAPI docs) | <http://localhost:8001/docs> |
| Voice media gateway | <http://localhost:8002/docs> |
| MinIO console | <http://localhost:9001> |
| Phoenix traces (platform profile) | <http://localhost:6006> |
| Expo mobile | the URL Expo prints |

When `OMNI_ALLOW_DEV_AUTH=true`, you can log in as `owner@omni.example`. The
development login endpoint returns 404 when this setting is off. Platform API
requests use the JWT that the development or production identity provider
returns.

## Feature guide

### Operations workflow

The web and mobile clients implement the complete Operations MVP. Users can:

1. search and maintain customers, contacts, service locations, and activity;
2. create jobs with notes and attachments;
3. maintain technicians and availability, then assign and reschedule work on a
   weekly calendar;
4. complete jobs and review their append-only status history;
5. draft and edit invoice lines in integer minor units, issue or void invoices,
   preview PDFs, and record payments; and
6. keep viewing cached records in read-only mode when the web client is
   offline.

Every Operations write goes through an explicit lifecycle command. Writes are
protected by tenant-scoped idempotency keys and optimistic versions. Each one
records an append-only audit event and a transactional outbox entry. Tests store
attachments on local disk; the container stack stores them in an S3-compatible
MinIO bucket.

### Omni Agent

The Assistant area on web and mobile runs durable, tenant-scoped AI sessions
and streams their progress as resumable server-sent events. Read tools run
immediately. Write tools, and tools whose effects are visible outside the
system, pause so that a human can approve, edit, or reject their typed
arguments. PostgreSQL stores each run so it can be replayed: tool inputs and
outputs, decisions, citations, feedback, model versions, token counts, cost,
audit records, and ordered run events.

`OMNI_AI_PROVIDER` selects the model provider: `local`, `gemini`, `vertex`,
`openai`, or `anthropic`. The built-in `local` provider is deterministic, which
makes development and CI repeatable. It supports these tools:

- customer search, job history, availability, and invoices;
- cited knowledge search;
- proposals for jobs, notes, schedules, and invoices; and
- queuing of approved outbound messages.

PII tokenization, the timeout, retry, and fallback policy, and the tool registry
work the same with every provider. The Assistant microphone records WebM/Opus
and sends it to the authenticated Gemini transcription endpoint. Gemini TTS can
read responses aloud.

Owners can index role-filtered knowledge from the Assistant. When the agent
extracts a lead, contact, or job request with low confidence, the result goes to
a human correction queue. The queue keeps the source offsets and the extractor
version.

### Omni Commerce (retail edition)

The retail edition was built for the Google Cloud AI Builder Cup 2026 (Retail &
Commerce track). Its demo tenant is Harbourline, a fictional Singapore lifestyle
retailer. It adds:

- an AI storefront where Gemini and Vertex AI Search answer shoppers' requests,
  personalized with each shopper's purchase history;
- merchant insights: demand forecasts (BigQuery `AI.FORECAST`), stock-out risk,
  RFM and k-means customer segments, and basket analysis; and
- Omni Agent tools that draft restock purchase orders and segment campaigns. A
  human must approve each one before it runs.

Every Google Cloud dependency has a local fallback, so the edition runs offline
with `make dev-retail`. To use the Google Cloud backends, set
`OMNI_RETAIL_SEARCH_BACKEND=vertex`, `OMNI_RETAIL_ANALYTICS_BACKEND=bigquery`,
and the `GOOGLE_CLOUD_*` variables. Then run `make retail-sync` to push the
catalog and analytics tables.
[docs/retail/GOOGLE_CLOUD.md](docs/retail/GOOGLE_CLOUD.md) covers the
architecture and the Cloud Run deployment.

### Business automations

The Automations workspace ships seven editable templates:

- lead intake;
- appointment reminders;
- missed-call follow-up;
- completion summaries;
- invoice drafts;
- overdue follow-up; and
- review escalation.

Definitions are versioned and start in shadow mode. Production actions support:

- human approval;
- tenant-scoped rate limits;
- retries and dead letters;
- replay and compensation; and
- bulk pause and an irreversible kill switch.

PostgreSQL stores the durable run history. The worker turns transactional
outbox events and interval schedules into Redis jobs.

### Voice pilot

The Voice workspace on web and mobile runs a limited, tenant-scoped intake flow
for after-hours calls. Each call:

- tells the caller that it uses AI and records the call, and asks for consent;
- detects emergency language;
- accepts DTMF keypad input;
- supports interruptions and streaming media; and
- transfers the caller when the flow cannot continue safely.

A callback job stays a proposal until an authorized person approves or edits
it.

The independent media service does the following:

- verifies signed, time-limited telephony webhooks and media tokens;
- records call events in order, with aligned transcript segments; and
- exposes a WebSocket/WebRTC bridge contract that any telephony, STT, realtime
  model, or TTS adapter can implement.

The included local adapters and call simulator are deterministic. To connect a
real carrier or hosted speech provider, you need credentials and an adapter
implementation. Recording and consent rules need legal review for each
jurisdiction before the system makes external calls.

### Call intelligence

The Intelligence workspace turns completed calls into replayable, governed data
products. For each call, the worker does the following:

1. finalizes a transcript aligned to each speaker;
2. redacts phone numbers and email addresses;
3. derives versioned results: topics, intent, sentiment, objections, compliance
   signals, actions, summaries, and outcomes; and
4. publishes a call fact that is ready to query.

Results with low confidence, and flagged results, go to human review.

Raw carrier webhooks stay unchanged in object storage. A transcript correction
creates a new revision instead of replacing the original. Provider snapshots
reconcile call status and duration. Every dashboard metric links back to its
call, transcript revision, extractor version, and pipeline checkpoints.

### Environmental workflow

The `omni-environment-mcp` service is a deliberately limited Python MCP server
built for an environmental project demonstration. Its resources and tools are
restricted to authorized users. They:

- search cited references;
- validate a synthetic laboratory XLSX workbook;
- compare results with screening criteria that are explicitly fictional; and
- prepare a report draft that cannot be published.

Project paths are limited to the selected workspace, and a qualified person must
review every report.

```bash
uv run python scripts/create_environment_demo_data.py  # generate the synthetic workbook
uv run pytest tests/test_environment_mcp.py
uv run omni-environment-mcp                            # stdio server
```

[mcp_config.json](mcp_config.json) is a sample configuration for registering the
server with an MCP client such as Claude Desktop. Change its paths to match your
checkout. To use a different authorized synthetic workspace, set
`OMNI_ENVIRONMENT_WORKSPACE`. Don't commit real client, laboratory, site, or
regulatory data to the repository.

The web app also has a tenant-scoped **Environmental** workspace. To use it:

1. create a project;
2. upload a laboratory XLSX workbook;
3. review the validation and screening observations, which are deterministic;
4. search the authorized demonstration references; and
5. generate a cited report draft.

Exported drafts carry a visible warning until an owner, administrator, or
reviewer records the reason for approving them. The app audits DOCX and XLSX
exports, source hashes, review decisions, and downloads.

### Observability

The Omni API and voice services send OpenTelemetry/OpenInference traces to the
Phoenix service in the `platform` Compose profile. Private data is filtered out
of the traces first. Tracing is off by default; to turn it on, set
`OMNI_TRACING_ENABLED=true`. Prompts and outputs are captured only when
`OMNI_TRACING_CAPTURE_CONTENT=true`. The
[Phoenix tracing runbook](docs/runbooks/phoenix-tracing.md) covers deployment,
security, trace structure, and troubleshooting.

`make monitoring-up` starts local Prometheus and Grafana.

## Configuration

All settings are in `.env` (see [env.example](env.example)). The main groups
are:

| Prefix / key | Purpose |
|---|---|
| `OMNI_DATABASE_URL`, `OMNI_REDIS_URL`, `OMNI_S3_*` | PostgreSQL, Redis, and attachment storage (`OMNI_STORAGE_BACKEND=local` or `s3`) |
| `OMNI_JWT_SECRET`, `OMNI_ALLOW_DEV_AUTH`, `OMNI_CORS_ORIGINS` | Auth and CORS |
| `OMNI_AI_*`, `OMNI_GEMINI_*` | Agent provider, model, rate limits, timeouts, STT/TTS |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Provider credentials |
| `OMNI_VOICE_*` | Voice webhook/media secrets and realtime budgets |
| `OMNI_TRACING_*` | Phoenix / OpenTelemetry export |
| `GOOGLE_CLOUD_*`, `OMNI_RETAIL_*`, `OMNI_BIGQUERY_*`, `OMNI_VERTEX_*`, `OMNI_GCS_BUCKET` | Retail edition on Google Cloud |
| `USER_API_KEY`, `DEFAULT_GOOGLE_MODEL`, `EVAL_*` | Legacy moderation app and evaluations |

## Tests

The default test suite is deterministic and never calls a model provider:

```bash
make test           # uv run pytest && npm test
npm run test:e2e    # Playwright; requires `make platform-up`
```

Live connectivity tests are opt-in and need a configured `.env`:

```bash
uv run pytest -m integration
```

Model-quality evaluations make live model calls, so their scores can vary
between runs:

```bash
uv run python evals/text/test_cases.py
uv run python evals/image/test_cases.py
uv run python evals/audio/test_cases.py
uv run python evals/video/test_cases.py
```

## Development workflow

```bash
make contracts       # regenerate OpenAPI and TypeScript contracts
make lint            # Python and TypeScript quality checks
make format          # isort, black, and prettier
make test            # deterministic Python and web tests
make build           # web and mobile production exports
make images          # build the production API, worker, and voice containers
make release-gate    # enforce quality, safety, drift, latency, and cost budgets
make infra-validate  # validate Terraform and stable/canary runtime manifests
make recovery-drill  # verify a checksummed PostgreSQL backup and disposable restore
```

To run one service at a time, use `make api`, `make worker`, `make voice`,
`make web`, or `make mobile`. To set up the database step by step, run
`make infra-up`, `make migrate`, and then `make seed` (or `make retail-seed`).

PostgreSQL, Redis, and MinIO store local data in named Docker volumes.
`make infra-down` stops the stack and keeps that data.

## Deployment

| Target | Entry point | Notes |
|---|---|---|
| Google Cloud (Omni Commerce) | `PROJECT_ID=<id> make deploy-gcp` | Cloud Run, Cloud SQL, Vertex AI, BigQuery. See [docs/retail/GOOGLE_CLOUD.md](docs/retail/GOOGLE_CLOUD.md). |
| EC2 staging | `scripts/deploy_ec2_staging.sh` | Runs on a single host with Caddy. See [deploy/ec2/README.md](deploy/ec2/README.md). |
| Kubernetes | `deploy/kubernetes/{base,canary}`, `infra/terraform` | EKS with stable/canary rollout through `scripts/canary_release.sh`. |

These documents describe the operating procedures and the decisions behind
them:

- [ADR 0008](docs/architecture/0008-scale-readiness.md)
- [voice reliability runbook](docs/runbooks/voice-reliability.md)
- [release and recovery runbook](docs/runbooks/release-and-recovery.md)

CI is defined in `.github/workflows/`.

## Repository layout

```
apps/web                React web client (Vite)
apps/mobile             Expo mobile client
packages/contracts      Generated OpenAPI schema and typed client
packages/ui             Shared UI primitives
services/api            FastAPI platform API, Alembic migrations, domain modules
services/worker         Background jobs and automation dispatcher
services/voice          Realtime voice media gateway
services/environment_mcp  Environmental MCP server
multimodal_moderation   Legacy moderation agents, Gradio and FastAPI apps
evals                   Model-quality evaluations and the platform release gate
deploy, infra           GCP, EC2, Kubernetes, monitoring, and Terraform
docs                    Architecture decision records, runbooks, retail guide
data/environment        Synthetic environmental demo data
tests                   Python test suite and Playwright e2e tests
```

## Legacy moderation proof of concept

The original multimodal moderation app (text, image, audio, and video agents) is
still available:

```bash
uv run multimodal-moderation       # Gradio chat + FastAPI + Phoenix
uv run multimodal-moderation-api   # FastAPI only
uv run multimodal-moderation-chat  # Gradio only
```

- Gradio chat: <http://localhost:7860>
- FastAPI documentation: <http://localhost:8000/docs>
- Phoenix traces: <http://localhost:6006/projects>

Moderation requests use `Authorization: Bearer <USER_API_KEY>`.
