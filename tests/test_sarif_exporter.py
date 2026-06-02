"""Unit tests for src.sarif_exporter."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from src.sarif_exporter import export_sarif


@dataclass
class MockThreat:
    category: str
    severity: str
    node_path: str
    confidence_score: float
    line_number: int


@dataclass
class MockFuncReport:
    function_key: str
    mahalanobis_distance: float
    renyi_entropy_discrete: float
    renyi_entropy_differential: float
    renyi_z_score: float
    anomaly_score: float


@dataclass
class MockModuleReport:
    mahalanobis_distance: float
    renyi_entropy_discrete: float
    renyi_entropy_differential: float
    renyi_z_score: float
    anomaly_score: float


@dataclass
class MockValidation:
    module_report: MockModuleReport
    function_reports: dict[str, MockFuncReport] = field(default_factory=dict)


@dataclass
class MockSecurity:
    threats: list[MockThreat] = field(default_factory=list)
    total_threats: int = 0


@dataclass
class MockFinalReport:
    validation: MockValidation
    security: MockSecurity
    unified_risk_score: float = 0.0
    risk_tier: str = "LOW"


class TestSarifExporter(unittest.TestCase):
    """Tests for SARIF output format validation."""

    def test_empty_report_structure(self) -> None:
        """An empty report should still produce valid SARIF skeleton."""
        report = MockFinalReport(
            validation=MockValidation(module_report=MockModuleReport(0, 0, 0, 0, 0)),
            security=MockSecurity(),
        )
        sarif = export_sarif(report, file_path="app.py")

        self.assertEqual(sarif["version"], "2.1.0")
        self.assertIn("$schema", sarif)
        self.assertIn("runs", sarif)
        self.assertEqual(len(sarif["runs"]), 1)
        run = sarif["runs"][0]
        self.assertIn("tool", run)
        self.assertEqual(run["tool"]["driver"]["name"], "Omni-Auditor")
        self.assertIn("results", run)
        self.assertEqual(len(run["results"]), 0)

    def test_security_finding_mapped(self) -> None:
        """Security findings should map to SARIF results with correct levels."""
        report = MockFinalReport(
            validation=MockValidation(module_report=MockModuleReport(0, 0, 0, 0, 0)),
            security=MockSecurity(
                threats=[
                    MockThreat("sql-injection", "CRITICAL", "cursor.execute", 0.95, 10),
                    MockThreat("path-traversal", "MEDIUM", "open(user_input)", 0.80, 20),
                ],
                total_threats=2,
            ),
        )
        sarif = export_sarif(report, file_path="app.py")
        results = sarif["runs"][0]["results"]

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["ruleId"], "sql-injection")
        self.assertEqual(results[0]["level"], "error")
        self.assertEqual(results[0]["locations"][0]["physicalLocation"]["region"]["startLine"], 10)

        self.assertEqual(results[1]["ruleId"], "path-traversal")
        self.assertEqual(results[1]["level"], "warning")
        self.assertEqual(results[1]["locations"][0]["physicalLocation"]["region"]["startLine"], 20)

    def test_structural_anomaly_threshold(self) -> None:
        """Only functions with z-score >= threshold should appear as anomalies."""
        report = MockFinalReport(
            validation=MockValidation(
                module_report=MockModuleReport(0, 0, 0, 0, 0),
                function_reports={
                    "high_z": MockFuncReport("high_z", 5.0, 0, 0, 2.0, 0),
                    "low_z": MockFuncReport("low_z", 1.0, 0, 0, 0.5, 0),
                },
            ),
            security=MockSecurity(),
        )
        sarif = export_sarif(report, file_path="app.py")
        results = sarif["runs"][0]["results"]

        anomaly_results = [r for r in results if r["ruleId"] == "structural-anomaly"]
        self.assertEqual(len(anomaly_results), 1)
        self.assertIn("high_z", anomaly_results[0]["message"]["text"])

    def test_module_anomaly_when_high(self) -> None:
        """Module-level anomaly should be emitted when module z-score is high."""
        report = MockFinalReport(
            validation=MockValidation(
                module_report=MockModuleReport(0, 0, 0, 2.5, 0),
            ),
            security=MockSecurity(),
        )
        sarif = export_sarif(report, file_path="app.py")
        results = sarif["runs"][0]["results"]

        module_anomalies = [
            r for r in results
            if r["ruleId"] == "structural-anomaly" and "Module-level" in r["message"]["text"]
        ]
        self.assertEqual(len(module_anomalies), 1)
        self.assertEqual(module_anomalies[0]["locations"][0]["physicalLocation"]["region"]["startLine"], 1)


if __name__ == "__main__":
    unittest.main()
