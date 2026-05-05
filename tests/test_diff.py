from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

import numpy as np
import pytest

# Ensure src/ is on the path when running pytest from repo root
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engine.baseline import BaselineManager, build_spectral_snapshot
from src.engine.diff import DeltaReport, SpectralDiffEngine
from src.main import AnalysisPipeline, FusionEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_full_pipeline(source_code: str) -> FusionEngine:
    """Run the full analysis pipeline and return the fused final report."""
    pipeline = AnalysisPipeline(source_code)
    analysis_result, security_result = await asyncio.gather(
        asyncio.to_thread(pipeline._run_analyzer),
        asyncio.to_thread(pipeline._run_security),
    )
    validation_result = await asyncio.to_thread(
        pipeline._run_validator, analysis_result
    )
    fusion = FusionEngine(analysis_result, validation_result, security_result)
    return fusion.fuse()


def _build_snapshot(project_id: str, final_report) -> dict:
    """Wrap :func:`build_spectral_snapshot` for convenience."""
    return build_spectral_snapshot(
        project_id=project_id,
        analysis=final_report.analysis,
        validation=final_report.validation,
        security=final_report.security,
        final_report=final_report,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDiff(unittest.TestCase):

    def test_identical_files_yield_near_zero_drift(self):
        """Two runs on the same source must produce drift_score ≈ 0 and STABLE."""
        code = "def foo():\n    return 1\n"
        final_a = asyncio.run(_run_full_pipeline(code))
        final_b = asyncio.run(_run_full_pipeline(code))

        snap_a = _build_snapshot("identical", final_a)
        snap_b = _build_snapshot("identical", final_b)

        engine = SpectralDiffEngine(snap_a, snap_b)
        delta = engine.compute("identical")

        assert isinstance(delta, DeltaReport)
        assert delta.drift_score < 0.05
        assert delta.risk_trend == "STABLE"

    def test_modified_file_shows_significant_drift(self):
        """Adding loops, functions, and security sinks must elevate drift_score above 0.2."""
        baseline_code = "def foo():\n    return 1\n"
        modified_code = '''
def foo():
    x = 0
    for i in range(10):
        x += i
        if x > 5:
            break
    return x

def bar(user_input):
    eval(user_input)
    os.system(user_input)
    return user_input
'''
        final_base = asyncio.run(_run_full_pipeline(baseline_code))
        final_mod = asyncio.run(_run_full_pipeline(modified_code))

        snap_base = _build_snapshot("modified", final_base)
        snap_mod = _build_snapshot("modified", final_mod)

        engine = SpectralDiffEngine(snap_base, snap_mod)
        delta = engine.compute("modified")

        assert isinstance(delta, DeltaReport)
        assert delta.drift_score > 0.2
        assert delta.risk_trend in ("DEGRADED", "FRACTURED")
        assert len(delta.function_changes) > 0

    def test_missing_baseline_raises_file_not_found(self):
        """Loading a non-existent baseline must raise FileNotFoundError."""
        import tempfile

        mgr = BaselineManager(baseline_dir=tempfile.mkdtemp())
        with pytest.raises(FileNotFoundError):
            mgr.load("nonexistent_project_xyz")
