"""Unit tests for src.engine.validator."""

from __future__ import annotations

import unittest

import numpy as np

from src.engine.validator import CovarianceEstimator, RenyiEntropyEstimator


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


if __name__ == "__main__":
    unittest.main()
