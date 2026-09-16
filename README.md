# Omni Model

Omni Model is a multi-tenant business automation platform with React web and Expo mobile clients, a modular FastAPI API, background processing, and multimodal AI moderation. The first platform slice manages customers with JWT authentication, tenant isolation, generated API contracts, and append-only audit events.

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
- Expo mobile: the URL displayed by Expo
- MinIO console: <http://localhost:9001>

## Operations workflow

The web and mobile clients implement the first auditable operations slice:

1. create a customer and job;
2. schedule the job using an offset-aware time;
3. complete the scheduled job;
4. draft an invoice with integer-minor-unit line totals; and
5. issue or void the invoice.

Operations writes use explicit lifecycle commands, tenant-scoped idempotency
keys, optimistic versions, append-only audit events, and a transactional
outbox. See
[ADR 0002](docs/architecture/0002-operations-domain-commands.md).

The legacy moderation proof of concept remains available through `uv run multimodal-moderation`:

- Gradio chat: <http://localhost:7860>
- FastAPI documentation: <http://localhost:8000/docs>
- Phoenix traces: <http://localhost:6006/projects>

## Tests

The default suite is deterministic and does not make model-provider requests:

```bash
uv run pytest
npm test
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
uv run omni-api
uv run omni-worker
```

Platform API requests use the JWT returned by the development or production identity provider. Legacy moderation requests continue to use `Authorization: Bearer <USER_API_KEY>`.

## Development workflow

```bash
make contracts  # regenerate OpenAPI and TypeScript contracts
make lint       # Python and TypeScript quality checks
make test       # deterministic Python and web tests
make build      # web and mobile production exports
make images     # build the production API and worker containers
```

PostgreSQL, Redis, and MinIO store local data in named Docker volumes. Use
`make infra-down` to stop the stack without deleting that data.

Development login uses `owner@omni.example` when `OMNI_ALLOW_DEV_AUTH=true`. This endpoint returns 404 when development authentication is disabled.
