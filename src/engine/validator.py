"""
Omni-Auditor — Statistical Validation Engine (validator.py)
==================================================================

This module consumes the spectral artefacts produced by ``analyzer.py`` and
applies rigorous multivariate statistical tests to quantify structural
anomalies at both module and per-function granularity.

Core components
---------------

1.  **CovarianceEstimator** — Computes the sample covariance matrix from a
    population of spectral feature vectors.  Implements **cold-start
    regularisation** (Σ + εI) where ε is data-adaptive and derived from the
    condition number of Σ.  Precision is obtained via Cholesky decomposition
    (``scipy.linalg.cho_solve``) with a pseudo-inverse fallback.

2.  **RenyiEntropyEstimator** — Evaluates spectral irregularity through:

    *   **Discrete Rényi-2 entropy** on the normalised Laplacian spectrum.
    *   **Differential Rényi-2 entropy** on the *weighted* spectral embedding
        ``E = U_k Λ_k^{1/2}`` under a Gaussian parametric assumption.

3.  **AnomalyScorer** — Fuses Mahalanobis distance and entropy deviations
    into a unified, scale-invariant anomaly score using standardised
    z-score aggregation.

4.  **StatisticalValidator** — High-level façade that accepts a
    ``StructuralAnalysisResult`` and emits a ``ValidationResult`` containing
    immutable dataclasses and fixed-dimension NumPy vectors for the UI
    orchestrator.

All public interfaces are strictly typed (``from __future__ import annotations``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.linalg import cho_factor, cho_solve

try:
    from .analyzer import Analyzer, SpectralProfile, StructuralAnalysisResult
except ImportError:  # pragma: no cover
    from analyzer import Analyzer, SpectralProfile, StructuralAnalysisResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EPS: float = 1e-12
_COV_COND_THRESHOLD: float = 1e12
_COV_DET_THRESHOLD: float = 1e-18
_RENYI_ALPHA: float = 2.0
_EMBED_DIM: int = 8

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FunctionAnomalyReport:
    """Per-function anomaly characterization."""

    function_key: str
    mahalanobis_distance: float
    renyi_entropy_discrete: float
    renyi_entropy_differential: float
    renyi_z_score: float
    anomaly_score: float
    raw_feature_vector: NDArray[np.float64]


@dataclass(frozen=True)
class ModuleAnomalyReport:
    """Module-level anomaly characterization."""

    mahalanobis_distance: float
    renyi_entropy_discrete: float
    renyi_entropy_differential: float
    renyi_z_score: float
    anomaly_score: float


@dataclass(frozen=True)
class ValidationResult:
    """Top-level immutable payload exported to ``main.py``."""

    module_report: ModuleAnomalyReport
    function_reports: dict[str, FunctionAnomalyReport]
    aggregate_anomaly_vector: NDArray[np.float64]
    population_feature_matrix: NDArray[np.float64]
    population_keys: list[str]
    covariance_matrix: NDArray[np.float64]
    precision_matrix: NDArray[np.float64]
    anomaly_threshold: float = 1.5


# ---------------------------------------------------------------------------
# Covariance estimation with cold-start regularisation
# ---------------------------------------------------------------------------


class CovarianceEstimator:
    """Sample covariance with condition-number-aware Tikhonov regularisation.

    Fallbacks
    ---------
    * Fewer than 2 samples: raises ``ValueError`` — covariance is undefined.
    * ``N < D`` (under-sampled): logs a warning and returns a diagonal
      covariance matrix containing only the per-feature variances.  This is
      mathematically valid and avoids an invalid full-rank estimate.
    * NaN/Inf entries in the empirical covariance: raises ``RuntimeError``
      so callers do not silently propagate numerical garbage.

    When the population is small (cold-start) or the feature dimension is
    large relative to the sample count, the empirical covariance is singular
    or near-singular.  This estimator automatically inflates the diagonal by
    ``εI`` where ``ε = clip(1/cond(Σ), 1e-6, 1e-4) * mean(|eig(Σ)|)``.

    The precision matrix is obtained via Cholesky factorisation for
    numerical stability; if the factorisation fails we fall back to the
    Moore-Penrose pseudo-inverse.
    """

    def __init__(self, X: NDArray[np.float64]) -> None:
        self.X: NDArray[np.float64] = X
        self.n: int = X.shape[0]
        self.d: int = X.shape[1]
        self.mean: NDArray[np.float64] = np.mean(X, axis=0)
        self.covariance: NDArray[np.float64] = self._compute_covariance()
        self.regularized_covariance: NDArray[np.float64] = self._regularize(
            self.covariance
        )
        self._cho_decomp: Any | None = self._cholesky_decompose(
            self.regularized_covariance
        )
        self.precision: NDArray[np.float64] = self._compute_precision()

    # -- internal machinery ------------------------------------------------

    def _compute_covariance(self) -> NDArray[np.float64]:
        if self.n < 2:
            raise ValueError("Need at least 2 samples for covariance")
        if self.d == 0:
            return np.zeros((0, 0), dtype=np.float64)

        if self.n < self.d:
            logger.warning(
                "Population under-sampled (N=%d < D=%d); using diagonal-only covariance.",
                self.n,
                self.d,
            )
            variances = np.var(self.X, axis=0, ddof=1)
            return np.diag(variances)

        Xc = self.X - self.mean
        cov = (Xc.T @ Xc) / (self.n - 1)

        if not np.all(np.isfinite(cov)):
            raise RuntimeError("Covariance matrix contains NaN or Inf values")

        return cov

    def _regularize(self, Sigma: NDArray[np.float64]) -> NDArray[np.float64]:
        if self.d == 0:
            return Sigma
        eigvals = np.linalg.eigvalsh(Sigma)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            cond = eigvals[-1] / max(abs(eigvals[0]), _EPS)
        det = float(np.prod(eigvals))

        # Trigger regularisation if singular, near-singular, or under-sampled.
        if (
            cond > _COV_COND_THRESHOLD
            or abs(det) < _COV_DET_THRESHOLD
            or self.n < self.d
        ):
            epsilon_raw: float = float(np.clip(
                1.0 / cond if (np.isfinite(cond) and cond > 0) else 1e-4,
                1e-6, 1e-4
            ))
            mean_eig = float(np.mean(np.abs(eigvals))) if np.any(np.abs(eigvals) > _EPS) else 1.0
            epsilon = epsilon_raw * mean_eig
            Sigma = Sigma + epsilon * np.eye(self.d, dtype=np.float64)
        return Sigma

    def _cholesky_decompose(self, Sigma: NDArray[np.float64]) -> Any | None:
        try:
            return cho_factor(Sigma, lower=True)
        except Exception:
            return None

    def _compute_precision(self) -> NDArray[np.float64]:
        if self._cho_decomp is not None:
            return cho_solve(self._cho_decomp, np.eye(self.d, dtype=np.float64))
        return np.linalg.pinv(self.regularized_covariance)

    # -- public API --------------------------------------------------------

    def mahalanobis_squared(self, x: NDArray[np.float64]) -> float:
        """Squared Mahalanobis distance of a single observation."""
        diff = x - self.mean
        if self._cho_decomp is not None:
            y = cho_solve(self._cho_decomp, diff)
            return float(diff @ y)
        # Fallback to explicit precision product.
        y = self.precision @ diff
        return float(diff @ y)

    def batch_mahalanobis_squared(self, X: NDArray[np.float64]) -> NDArray[np.float64]:
        """Vectorised squared Mahalanobis distances for a batch."""
        diff = X - self.mean
        if self._cho_decomp is not None:
            Y = cho_solve(self._cho_decomp, diff.T)
            return np.sum(diff * Y.T, axis=1)
        return np.sum(diff @ self.precision * diff, axis=1)


# ---------------------------------------------------------------------------
# Rényi entropy estimators
# ---------------------------------------------------------------------------


class RenyiEntropyEstimator:
    """Rigorous Rényi entropy computations for spectral artefacts.

    References
    ----------
    *   Discrete entropy:  ``H_α = (1-α)^{-1} log( Σ p_i^α )``.
    *   Differential entropy (Gaussian parametric) on embedding ``E``:
        ``h_2 = (k/2) log(4π) + (1/2) log|Σ_E|``  where  ``Σ_E = E^T E / n``.
    """

    @staticmethod
    def discrete_spectrum(
        eigenvalues: NDArray[np.float64], alpha: float = _RENYI_ALPHA
    ) -> float:
        """Discrete Rényi entropy of order ``α`` on a normalised spectrum.

        Parameters
        ----------
        eigenvalues:
            Raw eigenvalues (typically Laplacian eigenvalues).  Negative
            entries are clipped to zero before normalisation.
        alpha:
            Entropy order.  Default 2.0 (collision entropy).
        """
        vals = np.maximum(eigenvalues, 0.0)
        s = float(np.sum(vals))
        if s < _EPS:
            return 0.0
        p = vals / s
        p = p[p > _EPS]
        if len(p) == 0:
            return 0.0
        if alpha == 2.0:
            return float(-np.log(np.sum(p**2)))
        return float((1.0 / (1.0 - alpha)) * np.log(np.sum(p**alpha)))

    @staticmethod
    def differential_embedding(
        eigenvectors: NDArray[np.float64],
        eigenvalues: NDArray[np.float64],
        k: int = _EMBED_DIM,
    ) -> float:
        r"""Differential Rényi-2 entropy of the weighted spectral embedding.

        The embedding is constructed from the first ``k`` non-trivial
        eigenvectors scaled by ``sqrt(λ)``:

        .. math::
            E = U_k \, \mathrm{diag}(\sqrt{\lambda_2}, \dots, \sqrt{\lambda_{k+1}})

        Under a Gaussian parametric assumption the differential Rényi-2
        entropy is:

        .. math::
            h_2 = \frac{k}{2}\log(4\pi) + \frac{1}{2}\log|\Sigma_E|
        """
        n = eigenvectors.shape[0]
        k_eff = min(k, n - 1)
        if k_eff <= 0:
            return 0.0

        # Skip the trivial constant eigenvector (index 0).
        U_k = eigenvectors[:, 1 : k_eff + 1]
        lam_k = np.maximum(eigenvalues[1 : k_eff + 1], _EPS)
        E = U_k * np.sqrt(lam_k)  # broadcasting over columns

        # Sample covariance of the n embedding points in R^{k_eff}.
        Sigma = (E.T @ E) / n
        sign, logdet = np.linalg.slogdet(Sigma)
        if sign <= 0:
            Sigma += _EPS * np.eye(k_eff, dtype=np.float64)
            sign, logdet = np.linalg.slogdet(Sigma)
            if sign <= 0:
                # Ultimate fallback: diagonal determinant.
                logdet = float(np.sum(np.log(np.diag(Sigma) + _EPS)))

        h2 = 0.5 * k_eff * np.log(4.0 * np.pi) + 0.5 * logdet
        return float(h2)


# ---------------------------------------------------------------------------
# Anomaly fusion
# ---------------------------------------------------------------------------


class AnomalyScorer:
    """Fuses Mahalanobis distance and Rényi entropy deviations.

    Each component is standardised to a z-score so that the fusion is
    dimensionless and robust to differing scales.
    """

    def __init__(
        self,
        mahalanobis_distances: NDArray[np.float64],
        renyi_discrete: NDArray[np.float64],
        renyi_differential: NDArray[np.float64],
    ) -> None:
        self.mahal: NDArray[np.float64] = mahalanobis_distances
        self.renyi_d: NDArray[np.float64] = renyi_discrete
        self.renyi_diff: NDArray[np.float64] = renyi_differential

    @staticmethod
    def _zscore(x: NDArray[np.float64]) -> NDArray[np.float64]:
        mu = float(np.mean(x))
        sigma = float(np.std(x))
        if sigma < _EPS:
            return np.zeros_like(x)
        return (x - mu) / sigma

    def compute_scores(self) -> NDArray[np.float64]:
        """Return a unified anomaly score for each population member.

        The combination weights are:
        *   Mahalanobis distance : 0.50
        *   Discrete Rényi z     : 0.25
        *   Differential Rényi z : 0.25
        """
        z_mahal = np.abs(self._zscore(self.mahal))
        z_renyi_d = np.abs(self._zscore(self.renyi_d))
        z_renyi_diff = np.abs(self._zscore(self.renyi_diff))
        return 0.5 * z_mahal + 0.25 * z_renyi_d + 0.25 * z_renyi_diff


# ---------------------------------------------------------------------------
# Main validator façade
# ---------------------------------------------------------------------------


class StatisticalValidator:
    """Accepts a ``StructuralAnalysisResult`` and produces anomaly reports.

    The validator treats the module plus every discovered function as a
    statistical population.  It computes:

    1.  Population covariance and Mahalanobis distances.
    2.  Discrete and differential Rényi entropies per member.
    3.  A fused anomaly score via ``AnomalyScorer``.
    """

    def __init__(self, analysis: StructuralAnalysisResult, anomaly_threshold: float = 1.5) -> None:
        warnings.warn(
            "StatisticalValidator is deprecated and will be removed in a future release. "
            "Use PopulationValidator for real population-based anomaly detection.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.analysis: StructuralAnalysisResult = analysis
        self.anomaly_threshold: float = anomaly_threshold
        self._population_keys: list[str] = []
        self._population_matrix: NDArray[np.float64] = np.zeros((0, 0))
        self._build_population()

    # -- population construction -------------------------------------------

    def _build_population(self) -> None:
        keys: list[str] = ["<module>"]
        vectors: list[NDArray[np.float64]] = [
            self.analysis.module_spectral.feature_vector
        ]
        for key, profile in self.analysis.function_spectrals.items():
            keys.append(key)
            vectors.append(profile.feature_vector)
        self._population_keys = keys
        if vectors:
            self._population_matrix = np.stack(vectors)
        else:
            self._population_matrix = np.zeros(
                (1, len(self.analysis.module_spectral.feature_vector)), dtype=np.float64
            )

    # -- Rényi helpers -----------------------------------------------------

    def _compute_renyi_discrete(self) -> NDArray[np.float64]:
        vals: list[float] = []
        vals.append(
            RenyiEntropyEstimator.discrete_spectrum(
                self.analysis.module_spectral.eigenvalues_combinatorial
            )
        )
        for profile in self.analysis.function_spectrals.values():
            vals.append(
                RenyiEntropyEstimator.discrete_spectrum(
                    profile.eigenvalues_combinatorial
                )
            )
        return np.array(vals, dtype=np.float64)

    def _compute_renyi_differential(self) -> NDArray[np.float64]:
        vals: list[float] = []
        vals.append(
            RenyiEntropyEstimator.differential_embedding(
                self.analysis.module_spectral.eigenvectors_combinatorial,
                self.analysis.module_spectral.eigenvalues_combinatorial,
            )
        )
        for profile in self.analysis.function_spectrals.values():
            vals.append(
                RenyiEntropyEstimator.differential_embedding(
                    profile.eigenvectors_combinatorial,
                    profile.eigenvalues_combinatorial,
                )
            )
        return np.array(vals, dtype=np.float64)

    # -- aggregate vector construction -------------------------------------

    def _build_aggregate_vector(
        self,
        module_report: ModuleAnomalyReport,
        func_reports: dict[str, FunctionAnomalyReport],
        scores: NDArray[np.float64],
    ) -> NDArray[np.float64]:
        base = np.array(
            [
                module_report.mahalanobis_distance,
                module_report.renyi_entropy_discrete,
                module_report.renyi_entropy_differential,
                module_report.anomaly_score,
            ],
            dtype=np.float64,
        )

        if not func_reports:
            pad = np.zeros(12, dtype=np.float64)
            return np.concatenate([base, pad])

        func_mahal = np.array(
            [r.mahalanobis_distance for r in func_reports.values()], dtype=np.float64
        )
        func_renyi = np.array(
            [r.renyi_entropy_discrete for r in func_reports.values()], dtype=np.float64
        )
        func_diff = np.array(
            [
                r.renyi_entropy_differential
                for r in func_reports.values()
            ],
            dtype=np.float64,
        )
        func_scores = np.array(
            [r.anomaly_score for r in func_reports.values()], dtype=np.float64
        )

        stats = np.array(
            [
                np.mean(func_mahal),
                np.max(func_mahal),
                np.std(func_mahal),
                np.mean(func_renyi),
                np.max(func_renyi),
                np.std(func_renyi),
                np.mean(func_diff),
                np.max(func_diff),
                np.std(func_diff),
                np.mean(func_scores),
                np.max(func_scores),
                np.std(func_scores),
            ],
            dtype=np.float64,
        )

        return np.concatenate([base, stats])

    # -- public entry ------------------------------------------------------

    def validate(self) -> ValidationResult:
        """Run the full statistical validation pipeline.

        Returns
        -------
        ValidationResult
            Immutable container with per-module / per-function anomaly
            reports, a 16-D aggregate anomaly vector, and the regularised
            covariance / precision matrices.
        """
        # 1. Mahalanobis distances
        cov_est = CovarianceEstimator(self._population_matrix)
        mahal_sq = cov_est.batch_mahalanobis_squared(self._population_matrix)
        mahal = np.sqrt(np.maximum(mahal_sq, 0.0))

        # 2. Rényi entropies
        renyi_disc = self._compute_renyi_discrete()
        renyi_diff = self._compute_renyi_differential()

        # 3. Fusion
        scorer = AnomalyScorer(mahal, renyi_disc, renyi_diff)
        scores = scorer.compute_scores()

        # 4. Reports
        module_report = ModuleAnomalyReport(
            mahalanobis_distance=float(mahal[0]),
            renyi_entropy_discrete=float(renyi_disc[0]),
            renyi_entropy_differential=float(renyi_diff[0]),
            renyi_z_score=float(np.abs(scorer._zscore(renyi_disc)[0])),
            anomaly_score=float(scores[0]),
        )

        func_reports: dict[str, FunctionAnomalyReport] = {}
        for idx, key in enumerate(self._population_keys[1:], start=1):
            func_reports[key] = FunctionAnomalyReport(
                function_key=key,
                mahalanobis_distance=float(mahal[idx]),
                renyi_entropy_discrete=float(renyi_disc[idx]),
                renyi_entropy_differential=float(renyi_diff[idx]),
                renyi_z_score=float(np.abs(scorer._zscore(renyi_disc)[idx])),
                anomaly_score=float(scores[idx]),
                raw_feature_vector=self._population_matrix[idx].copy(),
            )

        agg = self._build_aggregate_vector(module_report, func_reports, scores)

        return ValidationResult(
            module_report=module_report,
            function_reports=func_reports,
            aggregate_anomaly_vector=agg,
            population_feature_matrix=self._population_matrix.copy(),
            population_keys=list(self._population_keys),
            covariance_matrix=cov_est.regularized_covariance.copy(),
            precision_matrix=cov_est.precision.copy(),
            anomaly_threshold=self.anomaly_threshold,
        )


# ---------------------------------------------------------------------------
# Population-based validator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PopulationValidator:
    """Real population-based structural anomaly detection.

    Unlike :class:`StatisticalValidator`, which fabricates a population from a
    single file's module and functions, this class builds a population from a
    directory of Python files, fits a robust covariance model using
    Ledoit-Wolf shrinkage, and scores new files via Mahalanobis distance.

    Parameters
    ----------
    population_dir:
        Directory containing representative Python source files.
    min_population_size:
        Minimum number of ``.py`` files required to fit the model.
    cache_dir:
        Directory where population statistics are cached.
    """

    population_dir: Path
    min_population_size: int = 50
    cache_dir: Path = Path(".omni_cache")

    def __post_init__(self) -> None:
        object.__setattr__(self, "population_dir", Path(self.population_dir))
        object.__setattr__(self, "cache_dir", Path(self.cache_dir))

    @property
    def _stats_path(self) -> Path:
        return self.cache_dir / "population_stats.json"

    @staticmethod
    def _hash_file_list(paths: list[Path]) -> str:
        """SHA256 digest of the sorted list of file paths."""
        normalized = sorted(str(p) for p in paths)
        payload = json.dumps(normalized, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _build_population(self) -> NDArray[np.float64]:
        """Recursively analyse all ``.py`` files and stack their 14-D module vectors."""
        population_path = Path(self.population_dir)
        if not population_path.exists():
            raise FileNotFoundError(f"Population directory does not exist: {population_path}")

        files = sorted(population_path.rglob("*.py"))
        if len(files) < self.min_population_size:
            raise ValueError(
                f"Population too small: {len(files)} files (minimum {self.min_population_size})"
            )

        vectors: list[NDArray[np.float64]] = []
        for path in files:
            try:
                source = path.read_text(encoding="utf-8")
            except Exception as exc:  # pragma: no cover
                logger.warning("Skipping population file %s: %s", path, exc)
                continue
            try:
                analysis = Analyzer(source).analyze(use_cache=True)
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to analyse population file %s: %s", path, exc)
                continue
            vectors.append(analysis.module_spectral.feature_vector)

        if len(vectors) < self.min_population_size:
            raise ValueError(
                f"Only {len(vectors)} valid module vectors extracted "
                f"(minimum {self.min_population_size})"
            )

        return np.stack(vectors)

    def fit(self) -> None:
        """Fit the population model, loading from cache when the population is unchanged."""
        from sklearn.covariance import LedoitWolf

        population_files = sorted(Path(self.population_dir).rglob("*.py"))
        current_hash = self._hash_file_list(population_files)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self._stats_path.exists():
            try:
                with open(self._stats_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("file_hash") == current_hash:
                    object.__setattr__(self, "mean_", np.array(cached["mean"], dtype=np.float64))
                    object.__setattr__(self, "covariance_", np.array(cached["covariance"], dtype=np.float64))
                    object.__setattr__(self, "precision_", np.array(cached["precision"], dtype=np.float64))
                    object.__setattr__(self, "population_", np.zeros((0, 0), dtype=np.float64))
                    object.__setattr__(self, "file_hash_", current_hash)
                    logger.info("Loaded population statistics from cache.")
                    return
            except Exception as exc:  # pragma: no cover
                logger.warning("Population cache invalid, refitting: %s", exc)

        population = self._build_population()
        lw = LedoitWolf()
        lw.fit(population)

        object.__setattr__(self, "mean_", np.array(lw.location_, dtype=np.float64))
        object.__setattr__(self, "covariance_", np.array(lw.covariance_, dtype=np.float64))
        object.__setattr__(self, "precision_", np.linalg.pinv(self.covariance_))
        object.__setattr__(self, "population_", population.copy())
        object.__setattr__(self, "file_hash_", current_hash)

        payload = {
            "file_hash": current_hash,
            "mean": self.mean_.tolist(),
            "covariance": self.covariance_.tolist(),
            "precision": self.precision_.tolist(),
            "n_samples": int(population.shape[0]),
            "n_features": int(population.shape[1]),
        }
        with open(self._stats_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info("Fitted and cached population statistics (%d files).", population.shape[0])

    def score(self, file_vector: NDArray[np.float64]) -> float:
        """Return the squared Mahalanobis distance of *file_vector* from the population.

        The model must be fitted via :meth:`fit` before scoring.
        """
        if not hasattr(self, "mean_"):
            raise RuntimeError("PopulationValidator must be fitted before scoring.")

        diff = file_vector - self.mean_
        return float(diff @ self.precision_ @ diff)

    def validate(
        self,
        analysis: StructuralAnalysisResult,
        anomaly_threshold: float = 1.5,
    ) -> ValidationResult:
        """Score *analysis* against the fitted population and return a ValidationResult."""
        self.fit()
        score = self.score(analysis.module_spectral.feature_vector)

        module_report = ModuleAnomalyReport(
            mahalanobis_distance=float(np.sqrt(score)),
            renyi_entropy_discrete=0.0,
            renyi_entropy_differential=0.0,
            renyi_z_score=0.0,
            anomaly_score=float(score),
        )

        # Population-level validator has no per-function reports.
        base = np.array(
            [
                module_report.mahalanobis_distance,
                module_report.renyi_entropy_discrete,
                module_report.renyi_entropy_differential,
                module_report.anomaly_score,
            ],
            dtype=np.float64,
        )
        aggregate = np.concatenate([base, np.zeros(12, dtype=np.float64)])

        return ValidationResult(
            module_report=module_report,
            function_reports={},
            aggregate_anomaly_vector=aggregate,
            population_feature_matrix=getattr(self, "population_", np.zeros((0, 0))).copy(),
            population_keys=[],
            covariance_matrix=getattr(self, "covariance_", np.zeros((0, 0))).copy(),
            precision_matrix=getattr(self, "precision_", np.zeros((0, 0))).copy(),
            anomaly_threshold=anomaly_threshold,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CovarianceEstimator",
    "RenyiEntropyEstimator",
    "AnomalyScorer",
    "StatisticalValidator",
    "PopulationValidator",
    "FunctionAnomalyReport",
    "ModuleAnomalyReport",
    "ValidationResult",
]
