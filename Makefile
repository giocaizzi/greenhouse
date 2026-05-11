.PHONY: help install test lint format check coverage serve clean \
        docker-build docker-up docker-down docker-logs docker-shell \
        pre-commit-install pre-commit-run

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install workspace dependencies with uv
	uv sync

test: ## Run the test suite
	uv run pytest

lint: ## Run ruff lint
	uv run ruff check libs/ tests/

format: ## Apply ruff formatter
	uv run ruff format libs/ tests/

check: lint test ## Lint then test (CI gate)

coverage: ## Run tests with coverage report (fails under 60%)
	uv run pytest --cov=tuya_irrigation_core --cov=tuya_irrigation_server --cov=tuya_irrigation_cli --cov-report=term-missing --cov-fail-under=60

serve: ## Start the FastAPI server (API + web UI)
	uv run tuya-irrigation-server

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
