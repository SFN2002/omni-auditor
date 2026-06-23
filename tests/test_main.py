"""Tests for src/main.py orchestrator, FusionEngine, and CLI."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.engine.baseline import BaselineManager, build_spectral_snapshot
from src.engine.diff import SpectralDiffEngine, DeltaReport
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


# ---------------------------------------------------------------------------
# Part 1 — Fusion Engine
# ---------------------------------------------------------------------------


class TestFusionEngine(unittest.TestCase):
    """Adaptive weighting and risk scoring."""

    def test_weights_sum_to_one(self) -> None:
        code = "def foo():\n    return 1\n"
        final = asyncio.run(_run_full_pipeline(code))
        weights = final.fusion_weights
        self.assertAlmostEqual(float(sum(weights)), 1.0, places=5)

    def test_risk_tier_assignment(self) -> None:
        # eval() should produce a CRITICAL finding.  Include a function so the
        # statistical validator has at least two population samples.
        code = "def foo():\n    eval(user_input)\n"
        final = asyncio.run(_run_full_pipeline(code))
        self.assertEqual(final.risk_tier, "CRITICAL")

    def test_unified_risk_score_range(self) -> None:
        code = "def foo():\n    return 1\n"
        final = asyncio.run(_run_full_pipeline(code))
        self.assertGreaterEqual(final.unified_risk_score, 0.0)
        self.assertLessEqual(final.unified_risk_score, 1.0)

    def test_threshold_override(self) -> None:
        code = "def foo():\n    x = 0\n    for i in range(10):\n        x += i\n    return x\n"
        final = asyncio.run(_run_full_pipeline(code))
        fusion = FusionEngine(final.analysis, final.validation, final.security)

        report_07 = fusion.fuse(critical_threshold=0.7)
        report_99 = fusion.fuse(critical_threshold=0.99)

        self.assertIsInstance(report_07.risk_tier, str)
        self.assertIsInstance(report_99.risk_tier, str)
        # Both should be valid tier strings
        self.assertIn(report_07.risk_tier, ("LOW", "MEDIUM", "HIGH", "CRITICAL"))
        self.assertIn(report_99.risk_tier, ("LOW", "MEDIUM", "HIGH", "CRITICAL"))


# ---------------------------------------------------------------------------
# Part 2 — CLI Arguments
# ---------------------------------------------------------------------------


class TestCliArgs(unittest.TestCase):
    """End-to-end CLI invocation via subprocess."""

    def _write_temp_py(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".py")
        try:
            os.write(fd, content.encode())
        finally:
            os.close(fd)
        return Path(path)

    def test_json_flag_produces_json(self) -> None:
        temp_path = self._write_temp_py("def foo():\n    return 1\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--json"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            data = json.loads(result.stdout)
            self.assertIn("unified_risk_score", data)
            self.assertIn("risk_tier", data)
            self.assertIn("security_findings", data)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_threshold_flag_accepted(self) -> None:
        temp_path = self._write_temp_py("def foo():\n    return 1\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--json", "--threshold", "0.99"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            data = json.loads(result.stdout)
            self.assertIn("risk_tier", data)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_save_baseline_creates_snapshot(self) -> None:
        temp_path = self._write_temp_py("def foo():\n    return 1\n")
        baseline_file = _REPO_ROOT / ".omni_cache" / "baselines" / "cli-save-test.json"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--json", "--save-baseline", "cli-save-test"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            self.assertTrue(baseline_file.exists(), "Baseline snapshot was not created")
        finally:
            temp_path.unlink(missing_ok=True)
            baseline_file.unlink(missing_ok=True)

    def test_diff_loads_baseline_and_computes_drift(self) -> None:
        temp_path = self._write_temp_py("def foo():\n    return 1\n")
        baseline_file = _REPO_ROOT / ".omni_cache" / "baselines" / "cli-diff-test.json"
        try:
            # First, save a baseline
            result_save = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--json", "--save-baseline", "cli-diff-test"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result_save.returncode, 0, f"save baseline stderr: {result_save.stderr}")
            self.assertTrue(baseline_file.exists())

            # Now diff against it
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--json", "--diff", "cli-diff-test"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            data = json.loads(result.stdout)
            self.assertIn("delta", data)
            self.assertIn("drift_score", data["delta"])
            self.assertIn("risk_trend", data["delta"])
            self.assertIn(data["delta"]["risk_trend"], ("IMPROVED", "STABLE", "DEGRADED", "FRACTURED"))
        finally:
            temp_path.unlink(missing_ok=True)
            baseline_file.unlink(missing_ok=True)

    def test_quiet_produces_one_line_summary(self) -> None:
        temp_path = self._write_temp_py("def foo():\n    return 1\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--quiet"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            # One-line format: "<path>: TIER (score: 0.XX)"
            self.assertIn(":", result.stdout)
            self.assertIn("(score:", result.stdout)
            # No Rich markup in stdout.
            self.assertNotIn("[", result.stdout)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_quiet_with_json_produces_json(self) -> None:
        temp_path = self._write_temp_py("def foo():\n    return 1\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--quiet", "--json"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            data = json.loads(result.stdout)
            self.assertIn("risk_tier", data)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_json_includes_exit_code_note(self) -> None:
        temp_path = self._write_temp_py("eval(user_input)\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--json"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 2)
            data = json.loads(result.stdout)
            self.assertIn("exit_code", data)
            self.assertIn("exit_code_note", data)
            self.assertEqual(data["exit_code"], 2)
            self.assertIn("CRITICAL", data["exit_code_note"])
        finally:
            temp_path.unlink(missing_ok=True)

    def test_quiet_includes_exit_code_note(self) -> None:
        temp_path = self._write_temp_py("eval(user_input)\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--quiet"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("Exiting with code 2", result.stdout)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_exit_code_critical(self) -> None:
        temp_path = self._write_temp_py("eval(user_input)\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--quiet"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("CRITICAL", result.stdout)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_quiet_save_baseline_suppresses_rich_message(self) -> None:
        temp_path = self._write_temp_py("def foo():\n    return 1\n")
        baseline_file = _REPO_ROOT / ".omni_cache" / "baselines" / "cli-quiet-save-test.json"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", str(temp_path), "--quiet", "--save-baseline", "cli-quiet-save-test"],
                capture_output=True,
                text=True,
                cwd=str(_REPO_ROOT),
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            self.assertTrue(baseline_file.exists())
            # Rich success message should be suppressed.
            self.assertNotIn("Baseline saved", result.stdout)
        finally:
            temp_path.unlink(missing_ok=True)
            baseline_file.unlink(missing_ok=True)


    def test_analysis_pipeline_auto_skips_validator_without_population(self) -> None:
        """When no population is available the pipeline should skip the validator gracefully."""
        pipeline = AnalysisPipeline("def foo():\n    return 1\n")
        self.assertTrue(pipeline.skip_validator)

    def test_analysis_pipeline_respects_explicit_skip_validator_false(self) -> None:
        """An explicit skip_validator=False should not be overridden by auto-detection."""
        pipeline = AnalysisPipeline("def foo():\n    return 1\n", skip_validator=False)
        self.assertFalse(pipeline.skip_validator)


# ---------------------------------------------------------------------------
# Part 3 — Baseline Integration
# ---------------------------------------------------------------------------


class TestBaselineIntegration(unittest.TestCase):
    """Save / load / diff roundtrips."""

    def test_save_load_roundtrip(self) -> None:
        code = "def foo():\n    return 1\n"
        final = asyncio.run(_run_full_pipeline(code))

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = BaselineManager(baseline_dir=tmpdir)
            snapshot = build_spectral_snapshot(
                project_id="roundtrip",
                analysis=final.analysis,
                validation=final.validation,
                security=final.security,
                final_report=final,
            )
            mgr.save("roundtrip", snapshot)
            loaded = mgr.load("roundtrip")
            self.assertEqual(loaded["project_id"], "roundtrip")

    def test_diff_output_format(self) -> None:
        code_a = "def foo():\n    return 1\n"
        code_b = "def foo():\n    eval(user_input)\n    return 1\n"

        final_a = asyncio.run(_run_full_pipeline(code_a))
        final_b = asyncio.run(_run_full_pipeline(code_b))

        snap_a = build_spectral_snapshot(
            project_id="diff",
            analysis=final_a.analysis,
            validation=final_a.validation,
            security=final_a.security,
            final_report=final_a,
        )
        snap_b = build_spectral_snapshot(
            project_id="diff",
            analysis=final_b.analysis,
            validation=final_b.validation,
            security=final_b.security,
            final_report=final_b,
        )

        engine = SpectralDiffEngine(snap_a, snap_b)
        delta = engine.compute("diff")

        self.assertIsInstance(delta, DeltaReport)
        self.assertIn(delta.risk_trend, ("IMPROVED", "STABLE", "DEGRADED", "FRACTURED"))
        self.assertIsInstance(delta.drift_score, float)
        self.assertGreaterEqual(delta.drift_score, 0.0)


if __name__ == "__main__":
    unittest.main()
