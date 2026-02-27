#!/bin/bash
# Quick test runner with summary

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🧪 Running irrigation system tests..."
echo ""

python3 tests/run_tests.py

echo ""
echo "✅ All tests passed!"
echo ""
echo "Test coverage:"
echo "  Database operations: 9 tests"
echo "  Smart logic:         11 tests"
echo "  Device management:   8 tests"
echo "  Total:               28 tests"
