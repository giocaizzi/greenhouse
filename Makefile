.PHONY: help install test lint format check check-fast coverage serve clean lint-imports typecheck sizecheck \
        docker-build docker-up docker-down docker-logs docker-shell \
        pre-commit-install pre-commit-run

.DEFAULT_GOAL := help

# Tests run in parallel (pytest-xdist); the serial suite takes about 20 minutes, parallel about 6 on 4 workers.
# Override on a shared host: make check PYTEST_XDIST="-n 2"; serial: PYTEST_XDIST=
# (Tests share no state; a single test, `uv run pytest path::name`, stays serial.)
PYTEST_XDIST ?= -n auto

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install workspace dependencies with uv
	uv sync

test: ## Run the test suite in parallel (PYTEST_XDIST="-n 2" on a shared host, PYTEST_XDIST= for serial)
	uv run pytest $(PYTEST_XDIST)

lint: ## Run ruff lint
	uv run ruff check libs/ tests/

format: ## Apply ruff formatter
	uv run ruff format libs/ tests/

check-fast: lint lint-imports typecheck sizecheck ## Quick gate (no tests): what the agents' stop hook runs; CI and `make check` keep the full suite

check: pre-commit-run lint-imports typecheck sizecheck coverage ## CI parity: pre-commit hooks (lint/format/hygiene) + import contracts + strict types + size limits + coverage gate

lint-imports: ## Check the import-linter layering contracts
	uv run lint-imports

typecheck: ## mypy strict over libs/ and tests/ (files and exclusions in [tool.mypy], pyproject.toml)
	uv run mypy

FILES ?= libs/
sizecheck: ## Size limits (body lines / nesting / file length); FILES=... to narrow, register in scripts/size-exceptions.txt
	uv run python scripts/sizecheck.py $(FILES)

coverage: ## Run tests in parallel with coverage report (fails under 60%)
	uv run pytest $(PYTEST_XDIST) --cov=greenhouse_core --cov=greenhouse_server --cov=greenhouse_cli --cov-report=term-missing --cov-report=xml --cov-fail-under=60

serve: ## Start the FastAPI server (API + web UI)
	uv run greenhouse-server

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage coverage.xml

docker-build: ## Build the runtime image
	docker compose build

docker-up: ## Start the server in the background
	docker compose up -d

docker-down: ## Stop and remove the server container
	docker compose down

docker-logs: ## Tail server logs
	docker compose logs -f --tail=100 server

docker-shell: ## Open a shell in the running server container
	docker compose exec server /bin/bash

pre-commit-install: ## Install pre-commit git hooks
	uvx pre-commit install

pre-commit-run: ## Run pre-commit on all tracked files
	uvx pre-commit run --all-files
