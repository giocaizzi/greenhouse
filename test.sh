#!/bin/bash
# Quick test runner with lint

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🧪 Running tests..."
.venv/bin/python3 tests/run_tests.py

echo ""
echo "🔍 Running linter..."
.venv/bin/ruff check src/ scripts/ tests/

echo ""
echo "✅ All checks passed!"
