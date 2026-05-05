"""
Run Omni-Auditor on every downloaded Python file and export metrics to CSV.

Metrics collected per file:
  - risk_score            : unified risk score from FusionEngine
  - security_findings_count : total threats from SecurityReport
  - structural_anomalies_count : functions with anomaly_score >= 0.5
  - risk_tier             : LOW / MEDIUM / HIGH / CRITICAL

Output: tests/benchmarks/results/omni_auditor_results.csv
"""

from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

# Ensure repo root is on sys.path so src.main can be imported.
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.main import OmniAuditor

BENCHMARK_DIR = Path(__file__).parent
DATASET_DIR = BENCHMARK_DIR / "dataset" / "downloaded"
RESULTS_DIR = BENCHMARK_DIR / "results"
CSV_PATH = RESULTS_DIR / "omni_auditor_results.csv"


def analyze_file(file_path: Path) -> dict[str, object]:
    """Run Omni-Auditor on a single file and return benchmark metrics."""
    source_code = file_path.read_text(encoding="utf-8")
    auditor = OmniAuditor(
        source_code,
        file_path=str(file_path),
        no_ui=True,
    )
    final_report = asyncio.run(auditor.run())

    risk_score = final_report.unified_risk_score
    security_findings_count = final_report.security.total_threats

    structural_anomalies_count = 0
    if final_report.validation and final_report.validation.function_reports:
        structural_anomalies_count = sum(
            1
            for rep in final_report.validation.function_reports.values()
            if rep.anomaly_score >= 0.5
        )

    return {
        "file_path": str(file_path.relative_to(BENCHMARK_DIR)),
        "risk_score": round(risk_score, 4),
        "security_findings_count": security_findings_count,
        "structural_anomalies_count": structural_anomalies_count,
        "risk_tier": final_report.risk_tier,
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    py_files = sorted(DATASET_DIR.rglob("*.py"))
    if not py_files:
        print("No Python files found in dataset. Run fetch_dataset.py first.")
        return

    print(f"Benchmarking Omni-Auditor on {len(py_files)} files ...")

    fieldnames = [
        "file_path",
        "risk_score",
        "security_findings_count",
        "structural_anomalies_count",
        "risk_tier",
    ]

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, py_file in enumerate(py_files, 1):
            print(f"  [{idx}/{len(py_files)}] {py_file.name}")
            try:
                result = analyze_file(py_file)
                writer.writerow(result)
            except Exception as exc:
                print(f"    ERROR: {exc}")
                writer.writerow(
                    {
                        "file_path": str(py_file.relative_to(BENCHMARK_DIR)),
                        "risk_score": 0.0,
                        "security_findings_count": 0,
                        "structural_anomalies_count": 0,
                        "risk_tier": "ERROR",
                    }
                )

    print(f"\nResults written to {CSV_PATH}")


if __name__ == "__main__":
    main()
