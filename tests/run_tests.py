#!/usr/bin/env python3
"""Test runner - executes all tests and reports results."""

import sys
import unittest
from pathlib import Path

# Add scripts to path


def run_tests():
    """Discover and run all tests."""
    loader = unittest.TestLoader()
    start_dir = Path(__file__).parent
    suite = loader.discover(start_dir, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
