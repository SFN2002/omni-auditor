#!/usr/bin/env python3
"""Benchmark Omni-Auditor against Bandit on a labelled dataset.

This script expects a dataset produced by ``collect_dataset.py`` and writes:

* ``benchmarks/data/benchmark_results.json`` — per-file predictions and metrics.
* ``benchmarks/data/BENCHMARKS.md`` — human-readable report.

Usage
-----
    python benchmarks/benchmark.py --dataset benchmarks/data/dataset.json

Metrics
-------
* Binary classification metrics (precision, recall, F1, accuracy) using
  HIGH/CRITICAL as the "vulnerable" prediction.
* Per-tier confusion matrix.
* Per-category precision/recall for Omni-Auditor security categories.
* Stratified 5-fold cross-validation scores.
* 95% bootstrap confidence intervals for the main F1 score.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.model_selection import StratifiedKFold

# Allow importing the package when running from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.cli import OmniAuditor


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DATASET: Path = Path("benchmarks/data/dataset.json")
DEFAULT_OUTPUT_JSON: Path = Path("benchmarks/data/benchmark_results.json")
DEFAULT_OUTPUT_MD: Path = Path("benchmarks/data/BENCHMARKS.md")
N_BOOTSTRAP: int = 1000
N_FOLDS: int = 5
RANDOM_SEED: int = 42


# ---------------------------------------------------------------------------
# Tool runners
# ---------------------------------------------------------------------------


async def _run_omni_auditor(source_code: str) -> dict[str, Any]:
    """Run Omni-Auditor via its Python API."""
    auditor = OmniAuditor(source_code, file_path="benchmark.py", no_ui=True)
    report = await auditor.run()
    return {
        "risk_tier": report.risk_tier,
        "unified_risk_score": report.unified_risk_score,
        "security_findings": [
            dataclasses.asdict(t) for t in report.security.threats
        ],
    }


def _run_bandit(file_path: str) -> dict[str, Any]:
    """Run Bandit and return parsed JSON."""
    venv_bandit = _REPO_ROOT / "venv" / "Scripts" / "bandit.exe"
    bandit_exe = str(venv_bandit) if venv_bandit.exists() else "bandit"
    result = subprocess.run(
        [bandit_exe, file_path, "-f", "json", "-q"],
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(result.stdout) if result.stdout else {"results": []}
    except json.JSONDecodeError:
        return {"results": []}


def _omni_flagged(prediction: dict[str, Any]) -> bool:
    """Return True if Omni-Auditor predicts vulnerable (HIGH/CRITICAL or any finding)."""
    tier = prediction.get("risk_tier", "")
    findings = prediction.get("security_findings", [])
    return tier in ("CRITICAL", "HIGH") or len(findings) > 0


def _bandit_flagged(prediction: dict[str, Any]) -> bool:
    """Return True if Bandit reported any issue."""
    return len(prediction.get("results", [])) > 0


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _confusion_matrix(labels: list[int], preds: list[int]) -> dict[str, int]:
    tp = fp = tn = fn = 0
    for y_true, y_pred in zip(labels, preds):
        if y_true and y_pred:
            tp += 1
        elif not y_true and y_pred:
            fp += 1
        elif y_true and not y_pred:
            fn += 1
        else:
            tn += 1
    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn}


def _metrics_from_confusion(cm: dict[str, int]) -> dict[str, float]:
    tp, fp, tn, fn = cm["TP"], cm["FP"], cm["TN"], cm["FN"]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / (tp + fp + tn + fn) if (tp + fp + tn + fn) else 0.0
    return {
        "Precision": precision,
        "Recall": recall,
        "F1": f1,
        "Accuracy": accuracy,
    }


def _compute_tier_confusion(
    labels: list[int], tiers: list[str]
) -> dict[str, dict[str, int]]:
    """Build a confusion matrix stratified by predicted risk tier."""
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for y_true, tier in zip(labels, tiers):
        matrix[tier]["VULNERABLE" if y_true else "BENIGN"] += 1
    return {tier: dict(counts) for tier, counts in matrix.items()}


def _compute_category_metrics(
    labels: list[int], predictions: list[dict[str, Any]]
) -> dict[str, dict[str, float]]:
    """Compute per-category precision/recall for Omni-Auditor categories."""
    categories: set[str] = set()
    for pred in predictions:
        for finding in pred.get("security_findings", []):
            categories.add(finding.get("category", "unknown"))

    metrics: dict[str, dict[str, float]] = {}
    for category in sorted(categories):
        preds = [
            any(
                f.get("category") == category
                for f in pred.get("security_findings", [])
            )
            for pred in predictions
        ]
        cm = _confusion_matrix(labels, [int(p) for p in preds])
        metrics[category] = _metrics_from_confusion(cm)
        metrics[category]["Support"] = sum(labels)
    return metrics


def _stratified_kfold_scores(
    labels: np.ndarray, omni_preds: np.ndarray, bandit_preds: np.ndarray
) -> dict[str, list[float]]:
    """Compute F1 scores across stratified folds."""
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_SEED)
    omni_f1s: list[float] = []
    bandit_f1s: list[float] = []
    for train_idx, test_idx in skf.split(np.zeros(len(labels)), labels):
        omni_cm = _confusion_matrix(
            labels[test_idx].tolist(), omni_preds[test_idx].tolist()
        )
        bandit_cm = _confusion_matrix(
            labels[test_idx].tolist(), bandit_preds[test_idx].tolist()
        )
        omni_f1s.append(_metrics_from_confusion(omni_cm)["F1"])
        bandit_f1s.append(_metrics_from_confusion(bandit_cm)["F1"])
    return {"omni": omni_f1s, "bandit": bandit_f1s}


def _bootstrap_ci(
    labels: np.ndarray, preds: np.ndarray, n_iter: int = N_BOOTSTRAP
) -> tuple[float, float, float]:
    """Return mean F1 and 95% CI via bootstrap."""
    rng = np.random.default_rng(RANDOM_SEED)
    scores: list[float] = []
    n = len(labels)
    for _ in range(n_iter):
        idx = rng.integers(0, n, size=n)
        cm = _confusion_matrix(labels[idx].tolist(), preds[idx].tolist())
        scores.append(_metrics_from_confusion(cm)["F1"])
    scores_arr = np.array(scores)
    return (
        float(np.mean(scores_arr)),
        float(np.percentile(scores_arr, 2.5)),
        float(np.percentile(scores_arr, 97.5)),
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _generate_markdown(
    dataset: dict[str, Any],
    omni_scores: dict[str, float],
    bandit_scores: dict[str, float],
    omni_cm: dict[str, int],
    bandit_cm: dict[str, int],
    tier_confusion: dict[str, dict[str, int]],
    category_metrics: dict[str, dict[str, float]],
    cv_scores: dict[str, list[float]],
    omni_ci: tuple[float, float, float],
    bandit_ci: tuple[float, float, float],
) -> str:
    lines: list[str] = [
        "# Omni-Auditor Benchmark Report",
        "",
        f"*Dataset*: `{dataset.get('total_files', 0)}` files  ",
        f"*Vulnerable*: `{dataset.get('vulnerable', 0)}` | *Benign*: `{dataset.get('benign', 0)}`",
        "",
        "## Aggregate Scores",
        "",
        "| Metric | Omni-Auditor | Bandit |",
        "|--------|-------------:|-------:|",
        f"| Precision | {omni_scores['Precision']:.3f} | {bandit_scores['Precision']:.3f} |",
        f"| Recall    | {omni_scores['Recall']:.3f} | {bandit_scores['Recall']:.3f} |",
        f"| F1        | {omni_scores['F1']:.3f} | {bandit_scores['F1']:.3f} |",
        f"| Accuracy  | {omni_scores['Accuracy']:.3f} | {bandit_scores['Accuracy']:.3f} |",
        "",
        "## Confusion Matrices",
        "",
        "### Omni-Auditor",
        "",
        "|       | Predicted Vulnerable | Predicted Benign |",
        "|-------|---------------------:|-----------------:|",
        f"| Vulnerable | {omni_cm['TP']} | {omni_cm['FN']} |",
        f"| Benign     | {omni_cm['FP']} | {omni_cm['TN']} |",
        "",
        "### Bandit",
        "",
        "|       | Predicted Vulnerable | Predicted Benign |",
        "|-------|---------------------:|-----------------:|",
        f"| Vulnerable | {bandit_cm['TP']} | {bandit_cm['FN']} |",
        f"| Benign     | {bandit_cm['FP']} | {bandit_cm['TN']} |",
        "",
        "## Per-Tier Confusion (Omni-Auditor)",
        "",
        "| Tier | Vulnerable | Benign |",
        "|------|-----------:|-------:|",
    ]
    for tier in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        counts = tier_confusion.get(tier, {})
        lines.append(
            f"| {tier} | {counts.get('VULNERABLE', 0)} | {counts.get('BENIGN', 0)} |"
        )

    lines += [
        "",
        "## Per-Category Metrics (Omni-Auditor)",
        "",
        "| Category | Precision | Recall | F1 |",
        "|----------|----------:|-------:|---:|",
    ]
    for category, metrics in sorted(category_metrics.items()):
        lines.append(
            f"| {category} | {metrics['Precision']:.3f} | "
            f"{metrics['Recall']:.3f} | {metrics['F1']:.3f} |"
        )

    lines += [
        "",
        "## Stratified 5-Fold Cross-Validation F1",
        "",
        f"* Omni-Auditor: mean={np.mean(cv_scores['omni']):.3f}, std={np.std(cv_scores['omni']):.3f}",
        f"* Bandit: mean={np.mean(cv_scores['bandit']):.3f}, std={np.std(cv_scores['bandit']):.3f}",
        "",
        "## Bootstrap 95% Confidence Intervals (F1)",
        "",
        f"* Omni-Auditor: {omni_ci[1]:.3f} — {omni_ci[2]:.3f} (mean={omni_ci[0]:.3f})",
        f"* Bandit: {bandit_ci[1]:.3f} — {bandit_ci[2]:.3f} (mean={bandit_ci[0]:.3f})",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run_all_omni(records: list[dict[str, Any]], cache_root: Path) -> list[dict[str, Any]]:
    """Run Omni-Auditor on all records concurrently (with bounded concurrency)."""
    semaphore = asyncio.Semaphore(8)
    results: list[dict[str, Any]] = []

    async def _analyze_one(record: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            cache_path = cache_root.parent / record["cache_path"]
            source = cache_path.read_text(encoding="utf-8")
            return await _run_omni_auditor(source)

    tasks = [_analyze_one(r) for r in records]
    return await asyncio.gather(*tasks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Omni-Auditor vs Bandit.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="Dataset JSON path.")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON, help="Results JSON path.")
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD, help="Markdown report path.")
    parser.add_argument("--skip-bandit", action="store_true", help="Skip Bandit comparison.")
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        print(f"Dataset not found: {args.dataset}. Run collect_dataset.py first.")
        return 1

    with open(args.dataset, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    records = dataset.get("records", [])
    if not records:
        print("Dataset is empty.")
        return 1

    print(f"Benchmarking {len(records)} files ...")

    cache_root = args.dataset.parent
    omni_predictions = asyncio.run(_run_all_omni(records, cache_root))

    bandit_predictions: list[dict[str, Any]] = []
    if not args.skip_bandit:
        print("Running Bandit ...")
        for record in records:
            cache_path = cache_root.parent / record["cache_path"]
            bandit_predictions.append(_run_bandit(str(cache_path)))
    else:
        bandit_predictions = [{"results": []} for _ in records]

    labels = np.array([1 if r["label"] == "VULNERABLE" else 0 for r in records], dtype=int)
    omni_flags = np.array([int(_omni_flagged(p)) for p in omni_predictions], dtype=int)
    bandit_flags = np.array([int(_bandit_flagged(p)) for p in bandit_predictions], dtype=int)

    omni_cm = _confusion_matrix(labels.tolist(), omni_flags.tolist())
    bandit_cm = _confusion_matrix(labels.tolist(), bandit_flags.tolist())
    omni_scores = _metrics_from_confusion(omni_cm)
    bandit_scores = _metrics_from_confusion(bandit_cm)

    tier_confusion = _compute_tier_confusion(
        labels.tolist(), [p.get("risk_tier", "LOW") for p in omni_predictions]
    )
    category_metrics = _compute_category_metrics(labels.tolist(), omni_predictions)
    cv_scores = _stratified_kfold_scores(labels, omni_flags, bandit_flags)
    omni_ci = _bootstrap_ci(labels, omni_flags)
    bandit_ci = _bootstrap_ci(labels, bandit_flags)

    per_file = [
        {
            "id": r["id"],
            "label": r["label"],
            "omni_flagged": bool(omni_flags[i]),
            "omni_tier": omni_predictions[i].get("risk_tier", "N/A"),
            "omni_findings": len(omni_predictions[i].get("security_findings", [])),
            "bandit_flagged": bool(bandit_flags[i]),
            "bandit_issues": len(bandit_predictions[i].get("results", [])),
        }
        for i, r in enumerate(records)
    ]

    results = {
        "dataset": args.dataset.name,
        "total_files": len(records),
        "vulnerable": int(labels.sum()),
        "benign": int(len(labels) - labels.sum()),
        "omni_auditor": {**omni_scores, **omni_cm},
        "bandit": {**bandit_scores, **bandit_cm},
        "tier_confusion": tier_confusion,
        "category_metrics": category_metrics,
        "cv_f1": cv_scores,
        "bootstrap_ci": {
            "omni": {"mean": omni_ci[0], "lower": omni_ci[1], "upper": omni_ci[2]},
            "bandit": {"mean": bandit_ci[0], "lower": bandit_ci[1], "upper": bandit_ci[2]},
        },
        "per_file": per_file,
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    md = _generate_markdown(
        dataset,
        omni_scores,
        bandit_scores,
        omni_cm,
        bandit_cm,
        tier_confusion,
        category_metrics,
        cv_scores,
        omni_ci,
        bandit_ci,
    )
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_md, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\nBenchmark complete.")
    print(f"  JSON: {args.output_json}")
    print(f"  MD  : {args.output_md}")
    print(f"  Omni F1: {omni_scores['F1']:.3f}")
    if not args.skip_bandit:
        print(f"  Bandit F1: {bandit_scores['F1']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
