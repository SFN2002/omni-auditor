# extracted from main.py
"""Adaptive fusion of spectral, statistical, and security signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

try:
    from .engine.analyzer import StructuralAnalysisResult
    from .engine.validator import ValidationResult
    from .engine.security import SecurityReport
except ImportError:  # pragma: no cover
    from engine.analyzer import StructuralAnalysisResult
    from engine.validator import ValidationResult
    from engine.security import SecurityReport


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalReport:
    """Immutable aggregate produced by the ``FusionEngine``."""

    analysis: StructuralAnalysisResult
    validation: ValidationResult
    security: SecurityReport
    fused_feature_vector: NDArray[np.float64]
    unified_risk_score: float
    risk_tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    fusion_weights: NDArray[np.float64]
    baseline_mode: bool = False


# ---------------------------------------------------------------------------
# Fusion Engine
# ---------------------------------------------------------------------------


class FusionEngine:
    """Adaptive fusion of spectral, statistical, and security signals.

    Inputs
    ------
    * ``analysis.aggregate_feature_vector`` — 56-D structural descriptor built
      from the module CFG plus aggregate function statistics (mean / max / std).
    * ``validation.aggregate_anomaly_vector`` — 16-D multivariate anomaly
      descriptor from the statistical validator (Mahalanobis + Rényi entropies).
    * ``security.feature_vector`` — 18-D categorical threat descriptor from the
      security scanner.

    Outputs
    -------
    A :class:`FinalReport` containing a unified risk score in ``[0, 1]`` and a
    discrete risk tier in ``{LOW, MEDIUM, HIGH, CRITICAL}``.

    Fusion philosophy
    -----------------
    Fixed weights are brittle: a clean file with one dangerous call would be
    drowned out by structural noise, while a trivial file with a critical
    vulnerability must surface immediately.  We therefore make the weights
    *severity-adaptive*.  When no security threats are present the three
    subsystems share weight roughly equally, letting structural anomalies
    dominate.  As severity rises, the security branch is up-weighted
    monotonically so that high-confidence threats cannot be masked by benign
    structure.  The validator is dropped (weight 0.0) when no population is
    available, and its share is redistributed to structure and security.

    The engine extracts the fixed-dimension vectors from each subsystem,
    applies the adaptive weights, concatenates the weighted components into a
    90-D fused feature vector, and synthesises the unified risk score through
    two non-linear gating functions.
    """

    def __init__(
        self,
        analysis: StructuralAnalysisResult,
        validation: ValidationResult,
        security: SecurityReport,
        skip_validator: bool = False,
    ) -> None:
        self.analysis = analysis
        self.validation = validation
        self.security = security
        self.skip_validator = skip_validator

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _clamp_vector(v: NDArray[np.float64], expected: int) -> NDArray[np.float64]:
        if v.shape[0] < expected:
            padded = np.zeros(expected, dtype=np.float64)
            padded[: v.shape[0]] = v
            return padded
        return v[:expected]

    def _pad_analysis_vector(self) -> NDArray[np.float64]:
        """Ensure the structural vector is 56-D without degrading functionless files.

        Files with no functions emit a 14-D module descriptor.  Rather than
        padding the remaining 42 dimensions with zeros, we repeat the module
        descriptor so the structural signal remains coherent.  This keeps the
        56-D representation semantically meaningful and avoids the previous
        "75% zeros" artefact that masked real structure from downstream scoring.
        """
        v = self.analysis.aggregate_feature_vector
        expected = 56
        if v.shape[0] >= expected:
            return v[:expected]

        module_vec = self.analysis.module_spectral.feature_vector
        padded = np.zeros(expected, dtype=np.float64)
        padded[: v.shape[0]] = v

        # For the canonical functionless case (14-D module vector), repeat the
        # module descriptor to fill the mean/max/std segments.
        if v.shape[0] == module_vec.shape[0]:
            segment = module_vec.shape[0]
            for offset in range(v.shape[0], expected, segment):
                end = min(offset + segment, expected)
                padded[offset:end] = module_vec[: end - offset]
        else:
            # Fallback: pad any other short vector with the scalar module mean.
            mean_val = float(np.mean(module_vec))
            padded[v.shape[0] :] = mean_val

        return padded

    def _compute_weights(self) -> tuple[float, float, float]:
        """Return adaptive 3-tuple (w_analysis, w_validator, w_security).

        The weights always sum to 1.0.  They are chosen from a severity ladder:

        * CRITICAL findings present → (0.15, 0.25, 0.60)
          Security dominates; structural signals provide context only.
        * HIGH findings present (no CRITICAL) → (0.20, 0.30, 0.50)
          Security still leads; validator gets a moderate share.
        * Otherwise → (0.30, 0.35, 0.35)
          Balanced tri-partite fusion when no severe threats are found.

        If ``skip_validator`` is set, the caller overrides these by zeroing the
        validator weight and re-normalising the remainder to (0.40, 0.60).
        """
        critical = self.security.severity_counts.get("CRITICAL", 0)
        high = self.security.severity_counts.get("HIGH", 0)

        # Escalate the security weight as severity increases.
        if critical > 0:
            return 0.15, 0.25, 0.60
        if high > 0:
            return 0.20, 0.30, 0.50
        return 0.30, 0.35, 0.35

    # -- public entry ------------------------------------------------------

    def fuse(self, critical_threshold: float = 0.7) -> FinalReport:
        a_vec = self._pad_analysis_vector()
        v_vec = self._clamp_vector(self.validation.aggregate_anomaly_vector, 16)
        s_vec = self._clamp_vector(self.security.feature_vector, 18)

        w_a, w_v, w_s = self._compute_weights()
        if self.skip_validator:
            w_v = 0.0
            w_a, w_s = 0.40, 0.60

        fused = np.concatenate([w_a * a_vec, w_v * v_vec, w_s * s_vec])

        # Gate 1: hard security signal
        critical = self.security.severity_counts.get("CRITICAL", 0)
        high     = self.security.severity_counts.get("HIGH", 0)
        medium   = self.security.severity_counts.get("MEDIUM", 0)
        security_score = float(np.tanh(0.6 * critical + 0.25 * high + 0.08 * medium))

        # Gate 2: structural anomaly (soft signal, normalised)
        structural_raw = float(np.linalg.norm(np.concatenate([w_a * a_vec, w_v * v_vec])))
        security_raw   = float(np.linalg.norm(w_s * s_vec))
        structural_score = float(np.tanh(max(structural_raw - security_raw, 0.0) / 15.0))

        # Blend
        if security_score > 0.05:
            unified = 0.75 * security_score + 0.25 * structural_score
        else:
            unified = 0.30 * structural_score

        unified = float(np.clip(unified, 0.0, 1.0))

        # Tier
        if critical > 0 or unified >= critical_threshold:
            tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "CRITICAL"
        elif high > 0 or unified >= 0.50:
            tier = "HIGH"
        elif medium > 0 or unified >= 0.25:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        return FinalReport(
            analysis=self.analysis,
            validation=self.validation,
            security=self.security,
            fused_feature_vector=fused,
            unified_risk_score=unified,
            risk_tier=tier,
            fusion_weights=np.array([w_a, w_v, w_s], dtype=np.float64),
            baseline_mode=not self.skip_validator,
        )
