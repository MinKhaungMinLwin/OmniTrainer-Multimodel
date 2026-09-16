.PHONY: setup infra-up infra-down platform-up platform-down migrate seed api worker web mobile dev test lint format contracts build images

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
	uv run omni-seed

api:
	uv run omni-api

worker:
	uv run omni-worker

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
	uv run omni-openapi
	npm run generate:contracts

build:
	npm run build

images:
	docker build -f services/api/Dockerfile -t omni-api:local .
	docker build -f services/worker/Dockerfile -t omni-worker:local .
