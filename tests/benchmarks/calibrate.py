"""
Omni-Auditor Calibration Script (Phase 3A)
==========================================

Samples real-world Python files from the benchmark dataset, runs the
Omni-Auditor CLI on each file, and computes percentile statistics over
the resulting unified risk scores.

The statistics drive threshold re-calibration so that "normal"
production code is not flagged as CRITICAL.

Usage::

    python tests/benchmarks/calibrate.py

Outputs::

    tests/benchmarks/calibration_data.json   # raw records + statistics
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BENCHMARK_DIR = Path(__file__).parent
DATASETS_DIR = BENCHMARK_DIR / "datasets"
MANIFEST_PATH = DATASETS_DIR / "manifest.json"
OUTPUT_PATH = BENCHMARK_DIR / "calibration_data.json"

MAX_PER_REPO = 10
MAX_TOTAL = 100
SEED = 42
TIMEOUT_SECONDS = 120
CLI = [sys.executable, "-m", "src.main", "--json"]

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationRecord:
    """Single observation from one analysed file."""

    file_path: str
    repo_name: str
    unified_risk_score: float
    risk_tier: str


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def load_manifest() -> list[dict[str, Any]]:
    """Read the benchmark manifest."""
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _repo_dir_name(repo_slug: str) -> str:
    """Convert 'owner/repo' to the local directory name used by fetch_dataset.py.

    fetch_dataset.py stores files under ``datasets/<repo>/`` where ``repo``
    is the repository name (second component of the slug).
    """
    return repo_slug.split("/", 1)[1]


def collect_files(manifest: list[dict[str, Any]]) -> list[tuple[str, Path]]:
    """Return ``(repo_name, file_path)`` tuples sampled per the spec.

    For each ``status == "ok"`` repository:
      * gather every ``*.py`` file under ``datasets/<repo>/``
      * randomly select up to ``MAX_PER_REPO`` files

    If the aggregate selection exceeds ``MAX_TOTAL``, a second random draw
    reduces the set to exactly ``MAX_TOTAL`` while preserving determinism.
    """
    rng = random.Random(SEED)
    selected: list[tuple[str, Path]] = []

    for entry in manifest:
        if entry.get("status") != "ok":
            continue

        repo_slug: str = entry["repo"]
        repo_name = _repo_dir_name(repo_slug)
        repo_dir = DATASETS_DIR / repo_name

        if not repo_dir.exists():
            continue

        py_files = sorted(repo_dir.rglob("*.py"))
        if not py_files:
            continue

        n = min(MAX_PER_REPO, len(py_files))
        sampled = rng.sample(py_files, n)
        selected.extend((repo_name, p) for p in sampled)

    if len(selected) > MAX_TOTAL:
        selected = rng.sample(selected, MAX_TOTAL)

    return selected


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def run_analysis(file_path: Path) -> CalibrationRecord | None:
    """Invoke the Omni-Auditor CLI and parse the JSON report.

    Returns ``None`` if the CLI exits non-zero, emits invalid JSON, or
    exceeds the timeout.
    """
    try:
        result = subprocess.run(
            [*CLI, str(file_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        return CalibrationRecord(
            file_path=str(file_path),
            repo_name="",  # populated by the caller
            unified_risk_score=float(data["unified_risk_score"]),
            risk_tier=str(data["risk_tier"]),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile compatible with NumPy's default method.

    Parameters
    ----------
    sorted_values:
        Values sorted in ascending order.
    p:
        Percentile to compute (0–100).
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]

    idx = (n - 1) * (p / 100.0)
    lower = int(math.floor(idx))
    upper = int(math.ceil(idx))
    if lower == upper:
        return sorted_values[lower]

    frac = idx - lower
    return sorted_values[lower] * (1.0 - frac) + sorted_values[upper] * frac


def compute_statistics(records: list[CalibrationRecord]) -> dict[str, Any]:
    """Derive descriptive statistics from the collected records."""
    scores = sorted([r.unified_risk_score for r in records])
    tiers = [r.risk_tier for r in records]
    n = len(records)

    return {
        "count": n,
        "mean": float(np.mean(scores)),
        "median": float(np.median(scores)),
        "stddev": float(np.std(scores)),
        "P10": _percentile(scores, 10.0),
        "P25": _percentile(scores, 25.0),
        "P50": _percentile(scores, 50.0),
        "P75": _percentile(scores, 75.0),
        "P80": _percentile(scores, 80.0),
        "P90": _percentile(scores, 90.0),
        "P95": _percentile(scores, 95.0),
        "P99": _percentile(scores, 99.0),
        "tier_counts": {
            "LOW": tiers.count("LOW"),
            "MEDIUM": tiers.count("MEDIUM"),
            "HIGH": tiers.count("HIGH"),
            "CRITICAL": tiers.count("CRITICAL"),
        },
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_table(stats: dict[str, Any], errors: int) -> None:
    """Render a formatted console summary."""
    count: int = stats["count"]

    print("\n" + "=" * 58)
    print("           OMNI-AUDITOR CALIBRATION STATISTICS")
    print("=" * 58)
    print(f"  Files analysed : {count}")
    print(f"  Errors/skipped : {errors}")
    print(f"  Mean           : {stats['mean']:.4f}")
    print(f"  Median         : {stats['median']:.4f}")
    print(f"  StdDev         : {stats['stddev']:.4f}")
    print("-" * 58)
    for key in ("P10", "P25", "P50", "P75", "P80", "P90", "P95", "P99"):
        print(f"  {key:<14s} : {stats[key]:.4f}")
    print("-" * 58)
    print("  Tier distribution:")
    for tier, tier_count in stats["tier_counts"].items():
        pct = (tier_count / count) * 100.0 if count else 0.0
        print(f"    {tier:<10s} : {tier_count:>3d} ({pct:>5.1f}%)")
    print("=" * 58)


def print_recommendations(stats: dict[str, Any]) -> None:
    """Print proposed threshold values derived from the percentiles."""
    p50 = stats["P50"]
    p80 = stats["P80"]
    p95 = stats["P95"]

    print("\n" + "=" * 58)
    print("           THRESHOLD RECOMMENDATIONS")
    print("=" * 58)
    print("  Current thresholds:")
    print("    LOW < 0.40 | MEDIUM < 0.70 | HIGH < 0.90 | CRITICAL")
    print("")
    print("  Proposed data-driven thresholds (percentile-based):")
    print(f"    LOW < {p50:.3f}    | MEDIUM < {p80:.3f}   | HIGH < {p95:.3f}  | CRITICAL")
    print("")
    print("  Rationale:")
    print(f"    • P50  ({p50:.3f})  → median normal code scores below this")
    print(f"    • P80  ({p80:.3f})  → 80%% of benign files fall below this")
    print(f"    • P95  ({p95:.3f})  → top 5%% of benign files; HIGH cutoff")
    print("=" * 58)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_results(
    records: list[CalibrationRecord],
    stats: dict[str, Any],
    errors: int,
) -> None:
    """Write the raw calibration data to JSON."""
    payload = {
        "metadata": {
            "seed": SEED,
            "max_per_repo": MAX_PER_REPO,
            "max_total": MAX_TOTAL,
            "cli_command": " ".join(CLI),
            "errors": errors,
        },
        "statistics": stats,
        "records": [
            {
                "file_path": r.file_path,
                "repo_name": r.repo_name,
                "unified_risk_score": r.unified_risk_score,
                "risk_tier": r.risk_tier,
            }
            for r in records
        ],
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"\nRaw calibration data saved to {OUTPUT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("Omni-Auditor Calibration — Phase 3A")
    print("=" * 58)

    manifest = load_manifest()
    files = collect_files(manifest)
    total_selected = len(files)
    print(f"Selected {total_selected} file(s) from 'ok' repositories (seed={SEED}).\n")

    records: list[CalibrationRecord] = []
    errors = 0

    for idx, (repo_name, file_path) in enumerate(files, 1):
        print(
            f"  [{idx:>3d}/{total_selected}] {repo_name}/{file_path.name} ... ",
            end="",
            flush=True,
        )
        record = run_analysis(file_path)
        if record is None:
            print("ERROR (skipped)")
            errors += 1
            continue

        # Back-fill the repo name because run_analysis does not know it.
        record = CalibrationRecord(
            file_path=record.file_path,
            repo_name=repo_name,
            unified_risk_score=record.unified_risk_score,
            risk_tier=record.risk_tier,
        )
        records.append(record)
        print(f"{record.unified_risk_score:.4f} ({record.risk_tier})")

    if not records:
        print("\nNo successful analyses. Nothing to calibrate.")
        sys.exit(1)

    stats = compute_statistics(records)
    print_table(stats, errors)
    print_recommendations(stats)
    save_results(records, stats, errors)


if __name__ == "__main__":
    main()
