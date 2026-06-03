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

    The engine extracts the fixed-dimension vectors from each subsystem,
    applies domain-specific adaptive weights, concatenates the weighted
    components, and synthesises a unified risk score in ``[0, 1]``.
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

    def _compute_weights(self) -> tuple[float, float, float]:
        critical = self.security.severity_counts.get("CRITICAL", 0)
        high = self.security.severity_counts.get("HIGH", 0)

        if critical > 0:
            return 0.15, 0.25, 0.60
        if high > 0:
            return 0.20, 0.30, 0.50
        return 0.30, 0.35, 0.35

    # -- public entry ------------------------------------------------------

    def fuse(self, critical_threshold: float = 0.7) -> FinalReport:
        """Fuse the three sub-reports into a single ``FinalReport``."""
        # Fixed-dimension vectors (extracted from the engine dataclasses)
        a_vec = self._clamp_vector(self.analysis.aggregate_feature_vector, 56)
        v_vec = self._clamp_vector(self.validation.aggregate_anomaly_vector, 16)
        s_vec = self._clamp_vector(self.security.feature_vector, 18)

        w_a, w_v, w_s = self._compute_weights()

        if self.skip_validator:
            w_v = 0.0
            w_a = 0.55
            w_s = 0.45

        # Weighted concatenation preserves every dimension while scaling
        # each domain by its adaptive importance.
        fused = np.concatenate([w_a * a_vec, w_v * v_vec, w_s * s_vec])

        # Base score: L2 norm of the fused representation.
        score: float = float(np.linalg.norm(fused))

        # Additive security boost (non-linear escalation for criticals).
        critical = self.security.severity_counts.get("CRITICAL", 0)
        high = self.security.severity_counts.get("HIGH", 0)
        score += 0.5 * critical + 0.2 * high

        # Squash to [0, 1] via tanh for stable downstream interpretation.
        unified = float(np.tanh(score / 10.0))

        # Risk tier with security override priority.
        if critical > 0 or unified >= critical_threshold:
            tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "CRITICAL"
        elif high > 0 or unified >= 0.5:
            tier = "HIGH"
        elif unified >= 0.3:
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
