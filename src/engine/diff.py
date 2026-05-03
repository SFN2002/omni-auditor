from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class DeltaReport:
    """Immutable container for the output of the spectral diff engine."""

    project_id: str
    drift_score: float
    risk_trend: str
    per_metric_deltas: dict[str, float]
    function_changes: list[dict[str, Any]]


class SpectralDiffEngine:
    """Computes structural drift between two spectral analysis snapshots.

    The engine operates on plain Python dictionaries produced by
    :func:`baseline.build_spectral_snapshot` and returns a
    :class:`DeltaReport` with a composite drift score in ``[0, 1]``.
    """

    def __init__(self, baseline: dict[str, Any], current: dict[str, Any]) -> None:
        self.baseline = baseline
        self.current = current

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _to_array(data: list[float] | NDArray[np.float64]) -> NDArray[np.float64]:
        if hasattr(data, "tolist"):
            return np.asarray(data, dtype=np.float64)
        return np.array(data, dtype=np.float64)

    @staticmethod
    def _pad_matrix_to_shape(
        mat: NDArray[np.float64], target_shape: tuple[int, int]
    ) -> NDArray[np.float64]:
        if mat.shape == target_shape:
            return mat
        padded = np.zeros(target_shape, dtype=np.float64)
        rows = min(mat.shape[0], target_shape[0])
        cols = min(mat.shape[1], target_shape[1])
        padded[:rows, :cols] = mat[:rows, :cols]
        return padded

    def _laplacian_distance(self, base_dict: dict[str, Any], curr_dict: dict[str, Any]) -> float | None:
        base_data = base_dict.get("laplacian_data")
        curr_data = curr_dict.get("laplacian_data")

        if base_data is None and curr_data is None:
            return None

        # One side missing → distance to zero matrix (i.e. norm of the existing Laplacian)
        if base_data is None:
            curr_shape = tuple(curr_dict.get("laplacian_shape", [0, 0]))
            L_curr = self._to_array(curr_data)
            if L_curr.ndim == 1:
                L_curr = L_curr.reshape(curr_shape)
            return float(np.linalg.norm(L_curr, "fro"))
        if curr_data is None:
            base_shape = tuple(base_dict.get("laplacian_shape", [0, 0]))
            L_base = self._to_array(base_data)
            if L_base.ndim == 1:
                L_base = L_base.reshape(base_shape)
            return float(np.linalg.norm(L_base, "fro"))

        base_shape = tuple(base_dict.get("laplacian_shape", [0, 0]))
        curr_shape = tuple(curr_dict.get("laplacian_shape", [0, 0]))

        L_base = self._to_array(base_data)
        L_curr = self._to_array(curr_data)

        if L_base.ndim == 1:
            L_base = L_base.reshape(base_shape)
        if L_curr.ndim == 1:
            L_curr = L_curr.reshape(curr_shape)

        target_shape = (max(base_shape[0], curr_shape[0]), max(base_shape[1], curr_shape[1]))
        L_base_padded = self._pad_matrix_to_shape(L_base, target_shape)
        L_curr_padded = self._pad_matrix_to_shape(L_curr, target_shape)

        return float(np.linalg.norm(L_base_padded - L_curr_padded, "fro"))

    def _kl_eigenvalue_drift(self, base_dict: dict[str, Any], curr_dict: dict[str, Any]) -> float | None:
        base_eig = base_dict.get("eigenvalues_top20")
        curr_eig = curr_dict.get("eigenvalues_top20")

        if base_eig is None and curr_eig is None:
            return None

        # One side missing → significant drift proportional to log dimension
        if base_eig is None or curr_eig is None:
            existing = base_eig if base_eig is not None else curr_eig
            return math.log(max(len(existing), 2))

        p = self._to_array(base_eig)
        q = self._to_array(curr_eig)

        max_len = max(len(p), len(q))
        p = np.pad(p, (0, max_len - len(p)), mode="constant")
        q = np.pad(q, (0, max_len - len(q)), mode="constant")

        p = np.maximum(p, 0.0)
        q = np.maximum(q, 0.0)

        p_sum = float(np.sum(p))
        q_sum = float(np.sum(q))
        if p_sum < 1e-12 or q_sum < 1e-12:
            return 0.0

        p = p / p_sum
        q = q / q_sum

        eps = 1e-12
        kl = float(np.sum(p * np.log((p + eps) / (q + eps))))
        return max(0.0, kl)

    def _fiedler_shift_single(self, base_dict: dict[str, Any], curr_dict: dict[str, Any]) -> float | None:
        v_base = base_dict.get("fiedler_vector")
        v_curr = curr_dict.get("fiedler_vector")

        if v_base is None and v_curr is None:
            return None

        # One side missing → maximum possible shift
        if v_base is None or v_curr is None:
            return 1.0

        a = self._to_array(v_base)
        b = self._to_array(v_curr)

        max_len = max(len(a), len(b))
        a = np.pad(a, (0, max_len - len(a)), mode="constant")
        b = np.pad(b, (0, max_len - len(b)), mode="constant")

        norm_a = float(np.linalg.norm(a))
        norm_b = float(np.linalg.norm(b))
        if norm_a < 1e-12 or norm_b < 1e-12:
            return 0.0

        cos_sim = float(np.dot(a, b) / (norm_a * norm_b))
        return 1.0 - abs(cos_sim)

    # -- component computations ----------------------------------------------

    def _compute_laplacian_frobenius(self) -> float:
        distances: list[float] = []

        d = self._laplacian_distance(
            self.baseline.get("module", {}), self.current.get("module", {})
        )
        if d is not None:
            distances.append(d)

        funcs_base = self.baseline.get("functions", {})
        funcs_curr = self.current.get("functions", {})
        for key in set(funcs_base.keys()) | set(funcs_curr.keys()):
            d = self._laplacian_distance(funcs_base.get(key, {}), funcs_curr.get(key, {}))
            if d is not None:
                distances.append(d)

        return float(np.mean(distances)) if distances else 0.0

    def _compute_eigenvalue_drift(self) -> float:
        drifts: list[float] = []

        d = self._kl_eigenvalue_drift(
            self.baseline.get("module", {}), self.current.get("module", {})
        )
        if d is not None:
            drifts.append(d)

        funcs_base = self.baseline.get("functions", {})
        funcs_curr = self.current.get("functions", {})
        for key in set(funcs_base.keys()) | set(funcs_curr.keys()):
            d = self._kl_eigenvalue_drift(funcs_base.get(key, {}), funcs_curr.get(key, {}))
            if d is not None:
                drifts.append(d)

        return float(np.mean(drifts)) if drifts else 0.0

    def _compute_fiedler_shift(self) -> float:
        shifts: list[float] = []

        s = self._fiedler_shift_single(
            self.baseline.get("module", {}), self.current.get("module", {})
        )
        if s is not None:
            shifts.append(s)

        funcs_base = self.baseline.get("functions", {})
        funcs_curr = self.current.get("functions", {})
        for key in set(funcs_base.keys()) | set(funcs_curr.keys()):
            s = self._fiedler_shift_single(funcs_base.get(key, {}), funcs_curr.get(key, {}))
            if s is not None:
                shifts.append(s)

        return float(np.mean(shifts)) if shifts else 0.0

    def _compute_modularity_delta(self) -> float:
        deltas: list[float] = []

        deltas.append(
            abs(
                self.baseline.get("module", {}).get("modularity_score", 0.0)
                - self.current.get("module", {}).get("modularity_score", 0.0)
            )
        )

        funcs_base = self.baseline.get("functions", {})
        funcs_curr = self.current.get("functions", {})
        for key in set(funcs_base.keys()) | set(funcs_curr.keys()):
            q_base = funcs_base.get(key, {}).get("modularity_score", 0.0)
            q_curr = funcs_curr.get(key, {}).get("modularity_score", 0.0)
            deltas.append(abs(q_base - q_curr))

        return float(np.mean(deltas)) if deltas else 0.0

    def _compute_security_delta(self) -> dict[str, int]:
        base_sec = self.baseline.get("security", {}).get("severity_counts", {})
        curr_sec = self.current.get("security", {}).get("severity_counts", {})

        all_keys = set(base_sec.keys()) | set(curr_sec.keys())
        return {k: int(curr_sec.get(k, 0) - base_sec.get(k, 0)) for k in all_keys}

    # -- public entry --------------------------------------------------------

    def compute(self, project_id: str = "") -> DeltaReport:
        """Run the full spectral diff pipeline and return a :class:`DeltaReport`."""
        frob = self._compute_laplacian_frobenius()
        eig_drift = self._compute_eigenvalue_drift()
        fiedler_shift = self._compute_fiedler_shift()
        mod_delta = self._compute_modularity_delta()
        sec_delta = self._compute_security_delta()

        # --- normalisation to [0, 1] ----------------------------------------
        base_mod = self.baseline.get("module", {})
        curr_mod = self.current.get("module", {})
        base_shape = tuple(base_mod.get("laplacian_shape", [0, 0]))
        curr_shape = tuple(curr_mod.get("laplacian_shape", [0, 0]))
        ref_scale = max(base_shape[0], curr_shape[0], 1)

        # Frobenius: exponential scaling relative to matrix dimension
        norm_frob = 1.0 - math.exp(-frob / float(ref_scale))
        norm_frob = float(np.clip(norm_frob, 0.0, 1.0))

        # KL-style drift: sigmoid-like compression
        norm_eig = eig_drift / (eig_drift + 1.0)
        norm_eig = float(np.clip(norm_eig, 0.0, 1.0))

        # Fiedler shift is already bounded in [0, 1]
        norm_fiedler = float(np.clip(fiedler_shift, 0.0, 1.0))

        # Modularity range is roughly [-0.5, 1] → max delta ~1.5
        norm_mod = mod_delta / 1.5
        norm_mod = float(np.clip(norm_mod, 0.0, 1.0))

        # Security: total absolute changes
        total_sec_delta = sum(abs(v) for v in sec_delta.values())
        norm_sec = total_sec_delta / (total_sec_delta + 1.0)
        norm_sec = float(np.clip(norm_sec, 0.0, 1.0))

        drift_score = (
            0.30 * norm_frob
            + 0.25 * norm_eig
            + 0.25 * norm_fiedler
            + 0.10 * norm_mod
            + 0.10 * norm_sec
        )
        drift_score = float(np.clip(drift_score, 0.0, 1.0))

        # --- risk trend ------------------------------------------------------
        base_risk = self.baseline.get("risk", {}).get("unified_risk_score", 0.0)
        curr_risk = self.current.get("risk", {}).get("unified_risk_score", 0.0)
        risk_delta = curr_risk - base_risk

        if drift_score > 0.5:
            trend = "FRACTURED"
        elif risk_delta > 0.1:
            trend = "DEGRADED"
        elif risk_delta < -0.1:
            trend = "IMPROVED"
        else:
            trend = "STABLE"

        # --- function change list --------------------------------------------
        func_changes: list[dict[str, Any]] = []
        funcs_base = self.baseline.get("functions", {})
        funcs_curr = self.current.get("functions", {})
        for key in sorted(set(funcs_base.keys()) | set(funcs_curr.keys())):
            in_base = key in funcs_base
            in_curr = key in funcs_curr
            if in_base and not in_curr:
                func_changes.append({"function": key, "change": "REMOVED"})
            elif not in_base and in_curr:
                func_changes.append({"function": key, "change": "ADDED"})
            else:
                b = funcs_base[key]
                c = funcs_curr[key]
                block_delta = abs(c.get("block_count", 0) - b.get("block_count", 0))
                if block_delta > 0:
                    func_changes.append(
                        {"function": key, "change": f"MODIFIED(+{block_delta} blocks)"}
                    )

        per_metric: dict[str, float] = {
            "laplacian_frobenius": float(frob),
            "eigenvalue_drift": float(eig_drift),
            "fiedler_shift": float(fiedler_shift),
            "modularity_delta": float(mod_delta),
            "security_delta": float(total_sec_delta),
            "normalized_frobenius": norm_frob,
            "normalized_eigenvalue_drift": norm_eig,
            "normalized_fiedler_shift": norm_fiedler,
            "normalized_modularity_delta": norm_mod,
            "normalized_security_delta": norm_sec,
        }

        return DeltaReport(
            project_id=project_id,
            drift_score=drift_score,
            risk_trend=trend,
            per_metric_deltas=per_metric,
            function_changes=func_changes,
        )
