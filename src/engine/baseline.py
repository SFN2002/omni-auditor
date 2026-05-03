from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

try:
    from .analyzer import SpectralProfile, StructuralAnalysisResult
except ImportError:  # pragma: no cover
    from analyzer import SpectralProfile, StructuralAnalysisResult


class BaselineManager:
    """JSON-based persistence layer for spectral analysis snapshots.

    Each baseline is stored as a human-readable JSON file under
    ``.omni_cache/baselines/<project_id>.json``.
    """

    def __init__(self, baseline_dir: str | Path = ".omni_cache/baselines") -> None:
        self.baseline_dir: Path = Path(baseline_dir)
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

    def _project_path(self, project_id: str) -> Path:
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_id)
        return self.baseline_dir / f"{safe_id}.json"

    def save(self, project_id: str, spectral_data_dict: dict[str, Any]) -> None:
        """Persist a JSON-serializable spectral snapshot to disk."""
        path = self._project_path(project_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spectral_data_dict, f, indent=2, ensure_ascii=False)

    def load(self, project_id: str) -> dict[str, Any]:
        """Load a previously saved spectral snapshot.

        Raises
        ------
        FileNotFoundError
            If no baseline exists for the given ``project_id``.
        """
        path = self._project_path(project_id)
        if not path.exists():
            raise FileNotFoundError(
                f"No baseline found for project_id='{project_id}' at {path}"
            )
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def exists(self, project_id: str) -> bool:
        """Return ``True`` iff a baseline has been saved for *project_id*."""
        return self._project_path(project_id).exists()


def _extract_profile(profile: SpectralProfile) -> dict[str, Any]:
    """Convert a single :class:`SpectralProfile` into a JSON-safe dict."""
    n = profile.laplacian_combinatorial.shape[0]
    eig_vals = profile.eigenvalues_combinatorial
    top20 = eig_vals[:20].tolist() if len(eig_vals) >= 20 else eig_vals.tolist()

    fiedler_vec: list[float] = []
    if profile.eigenvectors_combinatorial.shape[1] > 1:
        fiedler_vec = profile.eigenvectors_combinatorial[:, 1].tolist()

    return {
        "laplacian_shape": list(profile.laplacian_combinatorial.shape),
        "laplacian_data": profile.laplacian_combinatorial.tolist(),
        "eigenvalues_top20": top20,
        "fiedler_vector": fiedler_vec,
        "modularity_score": float(profile.modularity_index),
        "graph_energy": float(profile.graph_energy),
        "block_count": int(n),
    }


def build_spectral_snapshot(
    project_id: str,
    analysis: StructuralAnalysisResult,
    validation: Any,
    security: Any,
    final_report: Any,
) -> dict[str, Any]:
    """Build a complete JSON-serializable snapshot from engine outputs.

    Parameters
    ----------
    project_id:
        Human-readable project identifier.
    analysis:
        Result from :class:`Analyzer`.
    validation:
        Result from :class:`StatisticalValidator` (duck-typed to avoid circular imports).
    security:
        Result from :class:`SafetyGuard` (duck-typed).
    final_report:
        The fused :class:`FinalReport` from ``main.py`` (duck-typed).
    """
    functions: dict[str, dict[str, Any]] = {}
    for key, profile in analysis.function_spectrals.items():
        functions[key] = _extract_profile(profile)

    return {
        "project_id": project_id,
        "functions": functions,
        "module": _extract_profile(analysis.module_spectral),
        "security": {
            "severity_counts": dict(getattr(security, "severity_counts", {})),
            "total_threats": int(getattr(security, "total_threats", 0)),
        },
        "vectors": {
            "aggregate_56d": analysis.aggregate_feature_vector.tolist(),
            "anomaly_16d": getattr(validation, "aggregate_anomaly_vector", np.zeros(16)).tolist(),
            "threat_18d": getattr(security, "feature_vector", np.zeros(18)).tolist(),
        },
        "risk": {
            "unified_risk_score": float(getattr(final_report, "unified_risk_score", 0.0)),
            "risk_tier": str(getattr(final_report, "risk_tier", "LOW")),
        },
    }
