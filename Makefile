.PHONY: setup infra-up infra-down platform-up platform-down migrate seed api worker voice web mobile dev test lint format contracts build images release-gate infra-validate recovery-drill monitoring-up

setup:
	uv sync --locked --dev
	npm ci

infra-up:
	docker compose up -d postgres redis minio

infra-down:
	docker compose --profile platform down

platform-up:
	docker compose --profile platform up -d --build --wait

platform-down:
	docker compose --profile platform down

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m services.api.omni_api.seed

api:
	uv run python -m services.api.omni_api.main

worker:
	uv run python -m services.worker.omni_worker.main

voice:
	uv run python -m services.voice.omni_voice.main

web:
	npm run dev:web

mobile:
	npm run dev:mobile

dev: infra-up migrate seed
	npm run dev

test:
	PYTHONDONTWRITEBYTECODE=1 uv run pytest
	npm test

lint:
	uv run black --check multimodal_moderation services tests evals
	uv run isort --check-only multimodal_moderation services tests evals
	uv run flake8 multimodal_moderation services tests evals
	npm run lint

format:
	uv run isort multimodal_moderation services tests evals
	uv run black multimodal_moderation services tests evals
	npm run format

contracts:
	uv run python -m services.api.omni_api.openapi
	npm run generate:contracts

build:
	npm run build

images:
	docker build -f services/api/Dockerfile -t omni-api:local .
	docker build -f services/worker/Dockerfile -t omni-worker:local .
	docker build -f services/voice/Dockerfile -t omni-voice:local .

release-gate:
	uv run python -m evals.platform.release_gate --baseline evals/platform/baseline.json --candidate evals/platform/candidate.json --thresholds evals/platform/thresholds.json

infra-validate:
	terraform -chdir=infra/terraform fmt -check -recursive
	terraform -chdir=infra/terraform init -backend=false
	terraform -chdir=infra/terraform validate
	kubectl kustomize deploy/kubernetes/base >/dev/null
	kubectl kustomize deploy/kubernetes/canary >/dev/null

recovery-drill:
	bash scripts/recovery_drill.sh

monitoring-up:
	docker compose --profile monitoring up -d prometheus grafana
