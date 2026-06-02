"""Unit tests for src.engine.baseline."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from src.engine.baseline import BaselineManager, build_spectral_snapshot
from src.engine.analyzer import Analyzer
from src.engine.validator import StatisticalValidator
from src.engine.security import SafetyGuard


class MockReport:
    unified_risk_score = 0.42
    risk_tier = "MEDIUM"


class TestBaselineManager(unittest.TestCase):
    """Tests for baseline save / load round-trip."""

    def setUp(self) -> None:
        self.mgr = BaselineManager(baseline_dir=".omni_cache/baselines_test")
        self.mgr.clear = lambda: None  # type: ignore[method-assign]
        self.project_id = "test-project-42"

    def tearDown(self) -> None:
        # Clean up test baseline files
        path = self.mgr._project_path(self.project_id)
        if path.exists():
            path.unlink()

    def test_save_load_roundtrip(self) -> None:
        """Saving and loading should return identical data."""
        source = "def foo(): pass\n"
        analysis = Analyzer(source).analyze(use_cache=False)
        validation = StatisticalValidator(analysis).validate()
        security = SafetyGuard(source).scan()
        snapshot = build_spectral_snapshot(
            project_id=self.project_id,
            analysis=analysis,
            validation=validation,
            security=security,
            final_report=MockReport(),
        )

        self.mgr.save(self.project_id, snapshot)
        self.assertTrue(self.mgr.exists(self.project_id))

        loaded = self.mgr.load(self.project_id)
        self.assertEqual(loaded["project_id"], snapshot["project_id"])
        self.assertEqual(loaded["risk"]["unified_risk_score"], snapshot["risk"]["unified_risk_score"])
        self.assertEqual(loaded["risk"]["risk_tier"], snapshot["risk"]["risk_tier"])
        np.testing.assert_array_almost_equal(
            np.array(loaded["vectors"]["aggregate_56d"]),
            np.array(snapshot["vectors"]["aggregate_56d"]),
        )

    def test_load_missing_raises(self) -> None:
        """Loading a non-existent baseline should raise FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.mgr.load("does-not-exist")

    def test_project_id_sanitisation(self) -> None:
        """Unsafe characters in project_id should be replaced."""
        unsafe_id = "proj/one:two"
        path = self.mgr._project_path(unsafe_id)
        self.assertNotIn("/", path.name)
        self.assertNotIn(":", path.name)


if __name__ == "__main__":
    unittest.main()
