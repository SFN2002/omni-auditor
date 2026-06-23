"""Unit tests for src.engine.validator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pytest

from src.engine.validator import CovarianceEstimator, PopulationValidator, RenyiEntropyEstimator


class TestValidator(unittest.TestCase):
    """Tests for covariance estimation, Mahalanobis distance, and Rényi entropy."""

    def test_covariance_regularization(self) -> None:
        """Verify Σ + εI is applied when the covariance is ill-conditioned."""
        # Nearly collinear data with n < d triggers regularisation
        X = np.array(
            [
                [1.0, 2.0, 3.0],
                [1.01, 2.01, 3.01],
            ],
            dtype=np.float64,
        )
        est = CovarianceEstimator(X)

        reg_diag = np.diag(est.regularized_covariance)
        raw_diag = np.diag(est.covariance)

        # Regularized diagonal should be larger (εI added)
        self.assertTrue(np.all(reg_diag >= raw_diag - 1e-12))

    def test_mahalanobis_distance(self) -> None:
        """Verify D_M(x) increases for an outlier versus an inlier."""
        X = np.array(
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [0.0, 1.0],
                [1.0, 1.0],
            ],
            dtype=np.float64,
        )
        est = CovarianceEstimator(X)

        inlier = np.array([0.5, 0.5], dtype=np.float64)
        outlier = np.array([10.0, 10.0], dtype=np.float64)

        d_in = est.mahalanobis_squared(inlier)
        d_out = est.mahalanobis_squared(outlier)

        self.assertGreater(d_out, d_in)

    def test_renyi_entropy(self) -> None:
        """Verify H₂ decreases when the spectrum becomes more concentrated."""
        uniform = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float64)
        concentrated = np.array([10.0, 0.1, 0.1, 0.1], dtype=np.float64)

        h_uniform = RenyiEntropyEstimator.discrete_spectrum(uniform)
        h_concentrated = RenyiEntropyEstimator.discrete_spectrum(concentrated)

        self.assertGreater(h_uniform, h_concentrated)

    def test_covariance_is_positive_semi_definite(self) -> None:
        """Verify the regularised covariance matrix has non-negative eigenvalues."""
        rng = np.random.default_rng(42)
        X = rng.standard_normal((20, 10), dtype=np.float64)
        est = CovarianceEstimator(X)
        eigvals = np.linalg.eigvalsh(est.regularized_covariance)
        self.assertTrue(np.all(eigvals >= -1e-12))

    def test_mahalanobis_is_non_negative(self) -> None:
        """Verify squared Mahalanobis distance is always >= 0."""
        rng = np.random.default_rng(7)
        X = rng.standard_normal((15, 8), dtype=np.float64)
        est = CovarianceEstimator(X)
        for _ in range(10):
            x = rng.standard_normal(8, dtype=np.float64)
            d2 = est.mahalanobis_squared(x)
            self.assertGreaterEqual(d2, 0.0)

    def test_covariance_fewer_than_two_samples_raises(self) -> None:
        """A single sample cannot define a covariance matrix."""
        X = np.array([[1.0, 2.0, 3.0]], dtype=np.float64)
        with self.assertRaises(ValueError):
            CovarianceEstimator(X)

    def test_covariance_under_sampled_uses_diagonal(self) -> None:
        """When N < D the estimator should fall back to diagonal variances."""
        rng = np.random.default_rng(9)
        X = rng.standard_normal((3, 5), dtype=np.float64)
        est = CovarianceEstimator(X)

        # Off-diagonal entries must be zero.
        off_diag = est.covariance - np.diag(np.diag(est.covariance))
        np.testing.assert_array_almost_equal(off_diag, np.zeros_like(off_diag))

        # Diagonal entries must be the sample variances.
        expected_var = np.var(X, axis=0, ddof=1)
        np.testing.assert_array_almost_equal(np.diag(est.covariance), expected_var)

    def test_covariance_nan_inf_raises(self) -> None:
        """NaN or Inf in the population must surface as a RuntimeError."""
        X = np.array([[1.0, 2.0], [np.nan, 3.0]], dtype=np.float64)
        with self.assertRaises(RuntimeError):
            CovarianceEstimator(X)


# ---------------------------------------------------------------------------
# PopulationValidator
# ---------------------------------------------------------------------------


class TestPopulationValidator(unittest.TestCase):
    """Population-based anomaly detection with real (synthetic) populations."""

    def _build_synthetic_population(self, tmp_path: Path, n: int = 100) -> Path:
        """Create *n* synthetic Python files and return the population directory."""
        population_dir = tmp_path / "population"
        population_dir.mkdir()
        rng = np.random.default_rng(42)
        for i in range(n):
            # Vary function count and simple statements to produce distinct CFGs.
            n_funcs = rng.integers(1, 5)
            lines = ["x = 1\n"]
            for j in range(n_funcs):
                lines.append(f"def func_{i}_{j}():\n")
                lines.append("    return 1\n")
            (population_dir / f"file_{i:03d}.py").write_text("".join(lines), encoding="utf-8")
        return population_dir

    def test_fit_and_score_with_100_files(self) -> None:
        """A population of 100 files should fit and score a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            population_dir = self._build_synthetic_population(Path(tmpdir), n=100)
            validator = PopulationValidator(population_dir=population_dir)
            validator.fit()

            self.assertTrue(hasattr(validator, "mean_"))
            self.assertTrue(hasattr(validator, "covariance_"))
            self.assertTrue(hasattr(validator, "precision_"))
            self.assertEqual(validator.mean_.shape[0], 14)

            # A file drawn from the same distribution should score reasonably.
            test_file = population_dir / "file_test.py"
            test_file.write_text("x = 1\ndef foo():\n    return 1\n", encoding="utf-8")
            from src.engine.analyzer import Analyzer

            analysis = Analyzer(test_file.read_text(encoding="utf-8")).analyze(use_cache=False)
            score = validator.score(analysis.module_spectral.feature_vector)
            self.assertGreaterEqual(score, 0.0)
            self.assertTrue(np.isfinite(score))

    def test_population_too_small_raises(self) -> None:
        """Fewer than min_population_size files must raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            population_dir = self._build_synthetic_population(Path(tmpdir), n=5)
            validator = PopulationValidator(population_dir=population_dir, min_population_size=50)
            with self.assertRaises(ValueError):
                validator.fit()

    def test_cache_refits_when_population_changes(self) -> None:
        """Adding a file should invalidate the cache and trigger a refit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            population_dir = self._build_synthetic_population(Path(tmpdir), n=100)
            cache_dir = Path(tmpdir) / "cache"
            validator = PopulationValidator(population_dir=population_dir, cache_dir=cache_dir)
            validator.fit()
            first_hash = validator.file_hash_

            # Add a new file to change the population hash.
            (population_dir / "file_extra.py").write_text("def extra():\n    pass\n", encoding="utf-8")
            validator2 = PopulationValidator(population_dir=population_dir, cache_dir=cache_dir)
            validator2.fit()
            self.assertNotEqual(first_hash, validator2.file_hash_)


if __name__ == "__main__":
    unittest.main()
