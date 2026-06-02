"""SARIF v2.1.0 exporter for GitHub Security Tab compatibility.

Maps Omni-Auditor security findings and structural anomalies to the SARIF
schema consumed by ``github/codeql-action/upload-sarif``.
"""

from __future__ import annotations

from typing import Any

try:
    from .main import FinalReport
except ImportError:  # pragma: no cover
    from main import FinalReport


_TOOL_NAME = "Omni-Auditor"
_TOOL_VERSION = "0.1.0"
_SCHEMA_URI = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
_ANOMALY_Z_THRESHOLD = 1.5


def _severity_to_level(severity: str) -> str:
    mapping = {
        "CRITICAL": "error",
        "HIGH": "error",
        "MEDIUM": "warning",
        "LOW": "note",
    }
    return mapping.get(severity, "warning")


def _make_security_result(threat: Any, artifact_uri: str) -> dict[str, Any]:
    return {
        "ruleId": threat.category,
        "level": _severity_to_level(threat.severity),
        "message": {
            "text": (
                f"{threat.category}: {threat.node_path} "
                f"(confidence={threat.confidence_score:.2f})"
            )
        },
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": artifact_uri},
                    "region": {"startLine": max(1, threat.line_number)},
                }
            }
        ],
    }


def _extract_line_from_key(function_key: str) -> int:
    try:
        return int(function_key.split("@")[1].split(":")[0])
    except (IndexError, ValueError):
        return 1


def _make_anomaly_result(report: Any, artifact_uri: str) -> dict[str, Any] | None:
    z = report.renyi_z_score
    if z < _ANOMALY_Z_THRESHOLD:
        return None
    line = _extract_line_from_key(report.function_key)
    return {
        "ruleId": "structural-anomaly",
        "level": "warning",
        "message": {
            "text": (
                f"Structural anomaly in {report.function_key} "
                f"(Anomaly Z={z:.2f}, Mahalanobis={report.mahalanobis_distance:.2f})"
            )
        },
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": artifact_uri},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def export_sarif(
    final_report: FinalReport, file_path: str | None = None
) -> dict[str, Any]:
    """Export a ``FinalReport`` to a SARIF v2.1.0 dict.

    Parameters
    ----------
    final_report:
        Immutable aggregate produced by the ``FusionEngine``.
    file_path:
        Optional path to the analysed file (used as the artifact URI).

    Returns
    -------
    dict
        SARIF v2.1.0 document compatible with GitHub's ``upload-sarif`` action.
    """
    artifact_uri = file_path or "analyzed-file"
    results: list[dict[str, Any]] = []

    # Security findings
    for threat in final_report.security.threats:
        results.append(_make_security_result(threat, artifact_uri))

    # Per-function structural anomalies
    for func_report in final_report.validation.function_reports.values():
        anomaly_result = _make_anomaly_result(func_report, artifact_uri)
        if anomaly_result is not None:
            results.append(anomaly_result)

    # Module-level anomaly
    mod = final_report.validation.module_report
    if mod.renyi_z_score >= _ANOMALY_Z_THRESHOLD:
        results.append(
            {
                "ruleId": "structural-anomaly",
                "level": "warning",
                "message": {
                    "text": (
                        f"Module-level structural anomaly "
                        f"(Anomaly Z={mod.renyi_z_score:.2f}, "
                        f"Mahalanobis={mod.mahalanobis_distance:.2f})"
                    )
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": artifact_uri},
                            "region": {"startLine": 1},
                        }
                    }
                ],
            }
        )

    return {
        "$schema": _SCHEMA_URI,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": _TOOL_NAME,
                        "version": _TOOL_VERSION,
                        "informationUri": "https://github.com/omni-auditor/omni-auditor",
                    }
                },
                "results": results,
            }
        ],
    }


__all__ = ["export_sarif"]
