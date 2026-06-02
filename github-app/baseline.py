"""Baseline loading and spectral drift computation for the GitHub App."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on path so ``src.engine`` imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.engine.baseline import BaselineManager
from src.engine.diff import SpectralDiffEngine

logger = logging.getLogger(__name__)


def _repo_to_baseline_id(repo_name: str) -> str:
    """Sanitise ``owner/repo`` into a safe baseline project ID."""
    return repo_name.replace("/", "-").lower()


def load_baseline(repo_name: str, baseline_dir: str = ".omni_cache/baselines") -> dict[str, Any] | None:
    """Load a saved baseline for the repository, if one exists."""
    mgr = BaselineManager(baseline_dir=baseline_dir)
    baseline_id = _repo_to_baseline_id(repo_name)
    try:
        return mgr.load(baseline_id)
    except FileNotFoundError:
        return None


def compute_drift(
    repo_name: str,
    results: list[dict[str, Any]],
    baseline_dir: str = ".omni_cache/baselines",
) -> dict[str, Any] | None:
    """Compute spectral drift between the saved baseline and the current PR state.

    Parameters
    ----------
    repo_name:
        ``owner/repo`` full name.
    results:
        List of analysis result dicts produced by ``analyzer.analyze_file``.
    baseline_dir:
        Directory where baselines are persisted.

    Returns
    -------
    dict | None
        ``{"trend": str, "score": float}`` or ``None`` if no baseline exists.
    """
    baseline_data = load_baseline(repo_name, baseline_dir)
    if baseline_data is None:
        return None

    # Rebuild the current snapshot from the PR analysis results.
    # We use the first result's raw report to build a minimal snapshot
    # that SpectralDiffEngine can consume.
    current_snapshot: dict[str, Any] = {"project_id": _repo_to_baseline_id(repo_name)}

    # Aggregate security findings across all changed files
    total_threats = 0
    severity_counts: dict[str, int] = {}
    for r in results:
        raw = r.get("raw_report", {})
        findings = raw.get("security_findings", [])
        total_threats += len(findings)
        for f in findings:
            sev = f.get("severity", "LOW")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

    current_snapshot["security"] = {
        "severity_counts": severity_counts,
        "total_threats": total_threats,
    }

    # Risk summary — average the unified risk score across files
    if results:
        avg_score = sum(r["risk_score"] for r in results) / len(results)
        # Derive tier from score
        if avg_score >= 0.7:
            tier = "CRITICAL"
        elif avg_score >= 0.5:
            tier = "HIGH"
        elif avg_score >= 0.3:
            tier = "MEDIUM"
        else:
            tier = "LOW"
    else:
        avg_score = 0.0
        tier = "LOW"

    current_snapshot["risk"] = {
        "unified_risk_score": avg_score,
        "risk_tier": tier,
    }

    # Module / function profiles are optional for a lightweight diff;
    # SpectralDiffEngine handles missing keys gracefully.
    current_snapshot["module"] = {}
    current_snapshot["functions"] = {}

    try:
        diff_engine = SpectralDiffEngine(baseline_data, current_snapshot)
        delta = diff_engine.compute(project_id=_repo_to_baseline_id(repo_name))
        return {
            "trend": delta.risk_trend,
            "score": delta.drift_score,
        }
    except Exception as exc:
        logger.error("Drift computation failed for %s: %s", repo_name, exc)
        return None
