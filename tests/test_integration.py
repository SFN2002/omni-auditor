"""End-to-end integration tests for the Omni-Auditor pipeline.

These tests exercise the public Python API (``OmniAuditor``) against real
temporary source files, ensuring that structural analysis, security scanning,
and risk fusion work together coherently.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.cli import OmniAuditor


async def _run_auditor(source_code: str) -> None:
    """Helper to run the async OmniAuditor pipeline."""
    auditor = OmniAuditor(source_code, file_path="test.py")
    return await auditor.run()


def test_vulnerable_file_produces_security_findings() -> None:
    """A file with a hard-coded password must yield at least one security finding."""
    code = '''
import os

def connect():
    password = "SuperSecret123!"
    return password

def helper_a():
    return 1

def helper_b():
    return 2

def helper_c():
    return 3
'''
    report = asyncio.run(_run_auditor(code))

    # Structural vector must be present and non-empty.
    assert report.analysis.aggregate_feature_vector is not None
    assert report.analysis.aggregate_feature_vector.shape[0] > 0

    # At least one security finding (hard-coded secret).
    assert report.security.total_threats >= 1
    assert len(report.security.threats) >= 1

    # Risk score is normalised to [0, 1].
    assert 0.0 <= report.unified_risk_score <= 1.0

    # Tier must be one of the four valid labels.
    assert report.risk_tier in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def test_clean_file_is_low_or_medium_tier() -> None:
    """A clean file with simple functions should not be escalated above MEDIUM."""
    code = '''
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b
'''
    report = asyncio.run(_run_auditor(code))

    assert report.security.total_threats == 0
    assert 0.0 <= report.unified_risk_score <= 1.0
    assert report.risk_tier in {"LOW", "MEDIUM"}


def test_pipeline_persists_and_reloads_from_cache(tmp_path: Path) -> None:
    """The cache manager should transparently persist and reload a result."""
    code = "def cached_func():\n    return 42\n"

    # Use a temporary cache directory to avoid polluting the project cache.
    from src.engine.analyzer import Analyzer

    analyzer = Analyzer(code)
    analyzer._cache_manager.cache_dir = tmp_path / "omni_cache"
    result_first = analyzer.analyze(use_cache=True)
    result_second = analyzer.analyze(use_cache=True)

    np.testing.assert_array_almost_equal(
        result_first.aggregate_feature_vector,
        result_second.aggregate_feature_vector,
    )
