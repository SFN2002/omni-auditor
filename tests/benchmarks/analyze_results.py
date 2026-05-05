"""
Read the Omni-Auditor benchmark CSV and print summary statistics.

Statistics computed:
  - Mean risk score
  - Percentage of files with CRITICAL risk tier
  - Percentage of files with structural anomalies (>0)
"""

from __future__ import annotations

import csv
from pathlib import Path

BENCHMARK_DIR = Path(__file__).parent
CSV_PATH = BENCHMARK_DIR / "results" / "omni_auditor_results.csv"


def main() -> None:
    if not CSV_PATH.exists():
        print(f"Results file not found: {CSV_PATH}")
        print("Run compare.py first.")
        return

    rows: list[dict[str, float | int | str]] = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("risk_tier") == "ERROR":
                continue
            rows.append(
                {
                    "risk_score": float(row["risk_score"]),
                    "security_findings_count": int(row["security_findings_count"]),
                    "structural_anomalies_count": int(row["structural_anomalies_count"]),
                    "risk_tier": row["risk_tier"],
                }
            )

    if not rows:
        print("No valid results to analyze.")
        return

    total = len(rows)
    mean_risk = sum(r["risk_score"] for r in rows) / total
    critical_pct = (
        sum(1 for r in rows if r["risk_tier"] == "CRITICAL") / total
    ) * 100
    anomalies_pct = (
        sum(1 for r in rows if r["structural_anomalies_count"] > 0) / total
    ) * 100
    mean_security = sum(r["security_findings_count"] for r in rows) / total
    mean_anomalies = sum(r["structural_anomalies_count"] for r in rows) / total

    print("=" * 62)
    print("           OMNI-AUDITOR BENCHMARK SUMMARY")
    print("=" * 62)
    print(f"  Total files analyzed            : {total}")
    print(f"  Mean risk score                 : {mean_risk:.4f}")
    print(f"  Mean security findings          : {mean_security:.2f}")
    print(f"  Mean structural anomalies       : {mean_anomalies:.2f}")
    print(f"  % files with CRITICAL tier      : {critical_pct:.2f}%")
    print(f"  % files with structural anomalies: {anomalies_pct:.2f}%")
    print("=" * 62)


if __name__ == "__main__":
    main()
