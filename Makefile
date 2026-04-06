.PHONY: test lint format check clean coverage

test:
	uv run pytest

lint:
	uv run ruff check libs/ tests/

format:
	uv run ruff format libs/ tests/

check: lint test

coverage:
	uv run pytest --cov=tuya_irrigation_core --cov=tuya_irrigation_server --cov=tuya_irrigation_cli --cov-report=term-missing --cov-fail-under=60

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
