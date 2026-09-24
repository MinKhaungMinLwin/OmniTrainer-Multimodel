# Omni Model

Omni Model is a multi-tenant business automation platform with React web and Expo mobile clients, a modular FastAPI API, background processing, and multimodal AI moderation. The first platform slice manages customers with JWT authentication, tenant isolation, generated API contracts, and append-only audit events.

## Business automations

The Automations workspace ships seven editable templates for lead intake,
appointment reminders, missed-call follow-up, completion summaries, invoice
drafts, overdue follow-up, and review escalation. Definitions are versioned and
start in shadow mode. Production actions support human approval, tenant-scoped
rate limits, retries, dead letters, replay, compensation, bulk pause, and an
irreversible kill switch. PostgreSQL holds durable run history while the worker
bridges transactional outbox events and interval schedules into Redis jobs.

See [ADR 0005](docs/architecture/0005-automation-runtime.md) for the execution
and recovery model.

## Voice pilot

The Voice workspace on web and mobile operates a constrained, tenant-scoped
after-hours intake flow. Calls disclose AI use and recording, require consent,
detect emergency language, accept DTMF, support interruption and streaming
media, and transfer when the flow cannot proceed safely. A callback job remains
a proposal until an authorized person approves or edits it.

The independent media service verifies signed, time-bounded telephony webhooks
and media tokens, records ordered call events and aligned transcript segments,
and exposes a provider-neutral WebSocket/WebRTC bridge contract for telephony,
STT, realtime model, and TTS adapters. The included local adapters and call
simulator are deterministic; connecting a carrier or hosted speech provider
requires credentials and an adapter implementation. Recording and consent rules
must receive jurisdiction-specific legal review before external calls.

See [ADR 0006](docs/architecture/0006-realtime-voice-pilot.md) for the safety,
media, and data-lifecycle design.

## Call intelligence

The Intelligence workspace turns completed calls into replayable, governed data
products. The worker finalizes speaker-aligned transcripts, redacts phone and
email identifiers, derives versioned topics, intent, sentiment, objections,
compliance signals, actions, summaries, and outcomes, then publishes a
query-ready call fact. Low-confidence or flagged results enter human review.

Raw carrier webhooks remain immutable in object storage, while transcript
corrections create new revisions rather than replacing evidence. Provider
snapshots reconcile status and duration, and every dashboard metric links back
to the call, transcript revision, extractor version, and pipeline checkpoints.
See [ADR 0007](docs/architecture/0007-call-data-intelligence.md).

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the staged product and platform roadmap.

## Requirements

- Python 3.12 or newer
- Node.js 22 or newer
- Docker with Compose for PostgreSQL, Redis, and MinIO
- [`uv`](https://docs.astral.sh/uv/)
- A Gemini API key for live model calls

On macOS, a lightweight Homebrew-based Docker runtime can be installed and
started with:

```bash
brew install docker docker-compose docker-buildx colima
colima start --cpu 2 --memory 4
```

If Homebrew's Compose plugin is not discovered, add
`/opt/homebrew/lib/docker/cli-plugins` to `cliPluginsExtraDirs` in
`~/.docker/config.json`.

## Local setup

```bash
uv sync --locked --dev
npm ci
cp env.example .env
```

Set `GEMINI_API_KEY` and choose a non-empty local `USER_API_KEY` in `.env`.
For the Omni Assistant, set `OMNI_AI_PROVIDER=gemini` and
`OMNI_AI_MODEL=gemini-3.5-flash-lite`. Leave `GOOGLE_GEMINI_BASE_URL` blank for
an AI Studio key. The Assistant microphone records WebM/Opus, sends it to the
authenticated Gemini transcription endpoint, and can read responses aloud
through Gemini TTS.

Start local infrastructure, migrate, and seed the development workspace:

```bash
make infra-up
make migrate
make seed
```

Alternatively, run the API and worker entirely in containers; migrations and
idempotent seed data run automatically:

```bash
make platform-up
```

Run the new platform API and web app together:

```bash
make dev
```

Run the mobile app separately:

```bash
make mobile
```

The platform services are available at:

- React web: <http://localhost:5173>
- Platform API: <http://localhost:8001/docs>
- Voice media gateway: <http://localhost:8002/docs>
- Expo mobile: the URL displayed by Expo
- MinIO console: <http://localhost:9001>

## Operations workflow

The web and mobile clients implement the complete Operations MVP:

1. search and maintain customers, contacts, service locations, and activity;
2. create jobs with notes and attachments;
3. maintain technicians and availability, then assign and reschedule work on a weekly calendar;
4. complete jobs and review their append-only status history;
5. draft and edit integer-minor-unit invoice lines, issue or void invoices, preview PDFs, and record payments; and
6. continue viewing cached records in read-only mode when the web client is offline.

Operations writes use explicit lifecycle commands, tenant-scoped idempotency
keys, optimistic versions, append-only audit events, and a transactional
outbox. Attachments use local storage in tests and an S3-compatible MinIO
bucket in the container stack. See
[ADR 0002](docs/architecture/0002-operations-domain-commands.md) and
[ADR 0003](docs/architecture/0003-operations-mvp-storage-and-payments.md).

## AI Copilot

The Assistant area on web and mobile provides durable, tenant-scoped AI runs
with resumable server-sent events. Read tools execute immediately; write and
externally visible tools pause for a human to approve, edit, or reject their
typed arguments. Tool inputs, outputs, decisions, citations, feedback, model
versions, token counts, cost, audit records, and ordered run events remain
replayable in PostgreSQL.

The built-in local provider makes development and CI deterministic. It supports
customer search, job history, availability, invoices, cited knowledge search,
job/note/schedule/invoice proposals, and approved outbound-message queuing.
Provider routing, PII tokenization, timeout/retry/fallback policy, and the tool
registry are independent of that provider. See
[ADR 0004](docs/architecture/0004-ai-copilot-runtime.md).

Owners can index role-filtered knowledge from the Assistant. Low-confidence
lead, contact, and job-request extraction results enter the human correction
queue with source offsets and extractor versions preserved.

The legacy moderation proof of concept remains available through `uv run multimodal-moderation`:

- Gradio chat: <http://localhost:7860>
- FastAPI documentation: <http://localhost:8000/docs>
- Phoenix traces: <http://localhost:6006/projects>

The current Omni API and voice services export privacy-filtered OpenTelemetry/OpenInference traces to the Phoenix
service in the platform Compose profile. See [the Phoenix tracing runbook](docs/runbooks/phoenix-tracing.md) for
deployment, security, trace structure, and troubleshooting.

## Tests

The default suite is deterministic and does not make model-provider requests:

```bash
uv run pytest
npm test
npm run test:e2e  # requires `make platform-up`
```

Live connectivity tests are opt-in and require a configured `.env`:

```bash
uv run pytest -m integration
```

Run model-quality evaluations explicitly; these use live model calls and may produce probabilistic scores:

```bash
uv run python evals/text/test_cases.py
uv run python evals/image/test_cases.py
uv run python evals/audio/test_cases.py
uv run python evals/video/test_cases.py
```

## Individual services

```bash
uv run multimodal-moderation-api
uv run multimodal-moderation-chat
uv run python -m services.api.omni_api.main
uv run python -m services.worker.omni_worker.main
uv run python -m services.voice.omni_voice.main
```

Platform API requests use the JWT returned by the development or production identity provider. Legacy moderation requests continue to use `Authorization: Bearer <USER_API_KEY>`.

## Development workflow

```bash
make contracts  # regenerate OpenAPI and TypeScript contracts
make lint       # Python and TypeScript quality checks
make test       # deterministic Python and web tests
make build      # web and mobile production exports
make images     # build the production API and worker containers
make release-gate   # enforce quality, safety, drift, latency, and cost budgets
make infra-validate # validate Terraform and stable/canary runtime manifests
make recovery-drill # verify a checksummed PostgreSQL backup and disposable restore
make monitoring-up  # start local Prometheus and Grafana
```

PostgreSQL, Redis, and MinIO store local data in named Docker volumes. Use
`make infra-down` to stop the stack without deleting that data.

Development login uses `owner@omni.example` when `OMNI_ALLOW_DEV_AUTH=true`. This endpoint returns 404 when development authentication is disabled.

Scale-readiness decisions and operating procedures are documented in
[ADR 0008](docs/architecture/0008-scale-readiness.md), the
[voice reliability runbook](docs/runbooks/voice-reliability.md), and the
[release/recovery runbook](docs/runbooks/release-and-recovery.md).
