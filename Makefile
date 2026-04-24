.PHONY: help install test lint format check coverage serve clean

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
