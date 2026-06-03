# extracted from main.py
"""Async orchestration, CLI argument parsing, and entry point."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.panel import Panel

try:
    from .engine.analyzer import Analyzer, StructuralAnalysisResult
    from .engine.validator import StatisticalValidator, ValidationResult, ModuleAnomalyReport
    from .engine.security import SafetyGuard, SecurityReport
    from .engine.baseline import BaselineManager, build_spectral_snapshot
    from .engine.diff import SpectralDiffEngine, DeltaReport
    from .reporting.json_exporter import JSONExporter
    from .fusion import FusionEngine, FinalReport
    from .ui import RichUIRenderer, _print_delta_report, _print_post_run_summary
except ImportError:  # pragma: no cover
    from engine.analyzer import Analyzer, StructuralAnalysisResult
    from engine.validator import StatisticalValidator, ValidationResult, ModuleAnomalyReport
    from engine.security import SafetyGuard, SecurityReport
    from engine.baseline import BaselineManager, build_spectral_snapshot
    from engine.diff import SpectralDiffEngine, DeltaReport
    from reporting.json_exporter import JSONExporter
    from fusion import FusionEngine, FinalReport
    from ui import RichUIRenderer, _print_delta_report, _print_post_run_summary

logger = logging.getLogger("omni_auditor")


# ---------------------------------------------------------------------------
# Analysis Pipeline
# ---------------------------------------------------------------------------


class AnalysisPipeline:
    """Async orchestration of the three analysis stages.

    Phase 1 (concurrent)::
        Analyzer(source)  ||  SecurityGuard(source)

    Phase 2 (sequential)::
        Validator(analysis_result)
    """

    def __init__(self, source_code: str, anomaly_threshold: float = 1.5, skip_validator: bool = False) -> None:
        self.source_code: str = source_code
        self.anomaly_threshold: float = anomaly_threshold
        self.skip_validator: bool = skip_validator

    # -- internal CPU-bound runners ----------------------------------------

    def _run_analyzer(self) -> StructuralAnalysisResult:
        return Analyzer(self.source_code).analyze()

    def _run_validator(self, analysis: StructuralAnalysisResult) -> ValidationResult:
        if self.skip_validator:
            empty_module = ModuleAnomalyReport(
                mahalanobis_distance=0.0,
                renyi_entropy_discrete=0.0,
                renyi_entropy_differential=0.0,
                renyi_z_score=0.0,
                anomaly_score=0.0,
            )
            return ValidationResult(
                module_report=empty_module,
                function_reports={},
                aggregate_anomaly_vector=np.zeros(16, dtype=np.float64),
                population_feature_matrix=np.zeros((0, 0), dtype=np.float64),
                population_keys=[],
                covariance_matrix=np.zeros((0, 0), dtype=np.float64),
                precision_matrix=np.zeros((0, 0), dtype=np.float64),
                anomaly_threshold=self.anomaly_threshold,
            )
        return StatisticalValidator(analysis, anomaly_threshold=self.anomaly_threshold).validate()

    def _run_security(self) -> SecurityReport:
        return SafetyGuard(self.source_code).scan()

    # -- public async entry ------------------------------------------------

    async def execute(self) -> tuple[StructuralAnalysisResult, ValidationResult, SecurityReport]:
        """Execute the full pipeline and return the three result objects."""
        # Independent work offloaded to threads so the event loop stays alive.
        analysis_future = asyncio.to_thread(self._run_analyzer)
        security_future = asyncio.to_thread(self._run_security)

        analysis_result, security_result = await asyncio.gather(
            analysis_future, security_future
        )

        # Dependent stage: validator needs the spectral profiles.
        validation_result = await asyncio.to_thread(
            self._run_validator, analysis_result
        )

        return analysis_result, validation_result, security_result


# ---------------------------------------------------------------------------
# High-level async façade
# ---------------------------------------------------------------------------


class OmniAuditor:
    """Async façade that drives the pipeline, collects results, and renders
    the live console UI."""

    def __init__(
        self,
        source_code: str,
        file_path: str | None = None,
        no_ui: bool = False,
        critical_threshold: float = 0.7,
        anomaly_threshold: float = 1.5,
    ) -> None:
        self.source_code: str = source_code
        self.file_path: str | None = file_path
        self.no_ui: bool = no_ui
        self.critical_threshold: float = critical_threshold
        baseline_mgr = BaselineManager()
        has_baseline = any(baseline_mgr.baseline_dir.glob("*.json"))
        self.pipeline = AnalysisPipeline(
            source_code,
            anomaly_threshold=anomaly_threshold,
            skip_validator=not has_baseline,
        )
        if not no_ui:
            self.renderer = RichUIRenderer(file_path=file_path, anomaly_threshold=anomaly_threshold)

    async def _run_pipeline(self) -> FinalReport:
        """Execute analysis without any UI side effects."""
        analysis_result, security_result = await asyncio.gather(
            asyncio.to_thread(self.pipeline._run_analyzer),
            asyncio.to_thread(self.pipeline._run_security),
        )
        validation_result = await asyncio.to_thread(
            self.pipeline._run_validator, analysis_result
        )
        fusion = FusionEngine(
            analysis_result, validation_result, security_result,
            skip_validator=self.pipeline.skip_validator,
        )
        return await asyncio.to_thread(fusion.fuse, self.critical_threshold)

    async def run(self) -> FinalReport:
        """Execute the full end-to-end pipeline with live rendering.

        Returns
        -------
        FinalReport
            Immutable aggregate containing all sub-reports, the fused 90-D
            feature vector, the unified risk score, and the adaptive weights.
        """
        if self.no_ui:
            return await self._run_pipeline()

        if self.pipeline.skip_validator:
            Console().print(
                Panel(
                    "No baseline found — spectral validator disabled.\n"
                    "Run with --save-baseline first for full drift analysis.\n"
                    "Security scanner is still active.",
                    title="[yellow]Warning[/yellow]",
                    border_style="yellow",
                )
            )

        with self.renderer.live():
            # Phase 1 — concurrent independent work
            self.renderer.update_stage("analyzer", "running")
            self.renderer.update_stage("security", "running")

            analysis_result, security_result = await asyncio.gather(
                asyncio.to_thread(self.pipeline._run_analyzer),
                asyncio.to_thread(self.pipeline._run_security),
            )

            self.renderer.update_stage("analyzer", "complete")
            self.renderer.update_stage("security", "complete")
            self.renderer.set_analysis_result(analysis_result)
            self.renderer.set_security_result(security_result)

            # Phase 2 — sequential dependent work
            self.renderer.update_stage("validator", "running")
            validation_result = await asyncio.to_thread(
                self.pipeline._run_validator, analysis_result
            )
            self.renderer.update_stage("validator", "complete")
            self.renderer.set_validation_result(validation_result)

            # Phase 3 — fusion
            self.renderer.update_stage("fusion", "running")
            fusion = FusionEngine(
                analysis_result, validation_result, security_result,
                skip_validator=self.pipeline.skip_validator,
            )
            final_report = await asyncio.to_thread(fusion.fuse, self.critical_threshold)
            self.renderer.update_stage("fusion", "complete")
            self.renderer.set_final_report(final_report)

            return final_report


# ---------------------------------------------------------------------------
# Verbose metrics printer
# ---------------------------------------------------------------------------


def _print_verbose_metrics(report: FinalReport) -> None:
    """Print detailed per-function spectral metrics to stdout."""
    logger.info("=== Detailed Per-Function Spectral Metrics ===")
    for key, profile in report.analysis.function_spectrals.items():
        logger.info("Function: %s", key)
        logger.info("  Laplacian shape : %s", profile.laplacian_combinatorial.shape)
        logger.info("  Fiedler value   : %.4f", profile.fiedler_value)
        logger.info("  Spectral radius : %.4f", profile.spectral_radius)
        logger.info("  Spectral gap    : %.4f", profile.spectral_gap)
        eig_vals = profile.eigenvalues_combinatorial
        if len(eig_vals) > 6:
            logger.info(
                "  Eigenvalues     : "
                "[%.4f, %.4f, ..., %.4f] (n=%d)",
                eig_vals[0], eig_vals[1], eig_vals[-1], len(eig_vals),
            )
        else:
            logger.info("  Eigenvalues     : %s", [float(v) for v in eig_vals])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="omni-auditor",
        description="Static analysis engine with spectral graph theory, "
                    "statistical validation, and security scanning.",
    )
    parser.add_argument(
        "file",
        type=str,
        help="Path to the Python source file to analyse.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Skip the Rich UI and emit a compact JSON report to stdout.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Override the CRITICAL risk tier threshold (default: 0.7).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed per-function spectral metrics before the final report.",
    )
    parser.add_argument(
        "--save-baseline",
        type=str,
        default=None,
        help="Persist the current analysis snapshot as a baseline under the given project ID.",
    )
    parser.add_argument(
        "--diff",
        type=str,
        default=None,
        help="Load a saved baseline and compute structural drift against the current file.",
    )
    parser.add_argument(
        "--anomaly-threshold",
        type=float,
        default=1.5,
        help="Z-score threshold for flagging structural anomalies (default: 1.5).",
    )
    args = parser.parse_args()

    source_path = Path(args.file)
    if not source_path.exists():
        Console(stderr=True).print(f"[red]Error:[/red] {source_path} does not exist.")
        sys.exit(1)

    try:
        source_code = source_path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        logger.error("Error reading file: %s", exc)
        Console(stderr=True).print(f"[red]Error reading file:[/red] {exc}")
        sys.exit(1)

    auditor = OmniAuditor(
        source_code,
        file_path=str(source_path.resolve()),
        no_ui=args.json,
        critical_threshold=args.threshold,
        anomaly_threshold=args.anomaly_threshold,
    )
    try:
        final_report = asyncio.run(auditor.run())
    except Exception as exc:  # pragma: no cover
        logger.error("Analysis failed: %s", exc)
        Console(stderr=True).print(f"[red]Analysis failed:[/red] {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Baseline & Diff workflow (runs after normal analysis)
    # ------------------------------------------------------------------
    baseline_mgr = BaselineManager()

    if args.save_baseline:
        snapshot = build_spectral_snapshot(
            project_id=args.save_baseline,
            analysis=final_report.analysis,
            validation=final_report.validation,
            security=final_report.security,
            final_report=final_report,
        )
        baseline_mgr.save(args.save_baseline, snapshot)
        console = Console()
        console.print(
            f"[bold green]Baseline saved for project '[/bold green]"
            f"[bold cyan]{args.save_baseline}[/bold cyan]"
            f"[bold green]'.[/bold green]"
        )

    if args.diff:
        try:
            baseline_data = baseline_mgr.load(args.diff)
        except FileNotFoundError as exc:
            logger.error("Baseline not found: %s", exc)
            Console(stderr=True).print(f"[red]Error:[/red] {exc}")
            sys.exit(1)

        current_snapshot = build_spectral_snapshot(
            project_id=args.diff,
            analysis=final_report.analysis,
            validation=final_report.validation,
            security=final_report.security,
            final_report=final_report,
        )
        diff_engine = SpectralDiffEngine(baseline_data, current_snapshot)
        delta_report = diff_engine.compute(project_id=args.diff)

        if args.json:
            exporter = JSONExporter()
            payload = {
                "file_path": str(source_path.resolve()),
                "unified_risk_score": final_report.unified_risk_score,
                "risk_tier": final_report.risk_tier,
                "fusion_weights": exporter.convert(final_report.fusion_weights),
                "per_function_metrics": [
                    exporter.convert(dataclasses.asdict(fr))
                    for fr in final_report.validation.function_reports.values()
                ],
                "security_findings": [
                    exporter.convert(dataclasses.asdict(t))
                    for t in final_report.security.threats
                ],
                "delta": {
                    "project_id": delta_report.project_id,
                    "drift_score": delta_report.drift_score,
                    "risk_trend": delta_report.risk_trend,
                    "per_metric_deltas": delta_report.per_metric_deltas,
                    "function_changes": delta_report.function_changes,
                },
            }
            print(json.dumps(payload, separators=(",", ":")))
            return

        _print_delta_report(delta_report)
        # Fall through to the normal post-run summary so the user sees both.

    if args.verbose:
        _print_verbose_metrics(final_report)

    if args.json and not args.diff:
        exporter = JSONExporter()
        payload = {
            "file_path": str(source_path.resolve()),
            "unified_risk_score": final_report.unified_risk_score,
            "risk_tier": final_report.risk_tier,
            "fusion_weights": exporter.convert(final_report.fusion_weights),
            "per_function_metrics": [
                exporter.convert(dataclasses.asdict(fr))
                for fr in final_report.validation.function_reports.values()
            ],
            "security_findings": [
                exporter.convert(dataclasses.asdict(t))
                for t in final_report.security.threats
            ],
        }
        # Emit to stdout for piping and persist to output.json for IDE integration.
        print(json.dumps(payload, separators=(",", ":")))
        output_path = Path("output.json")
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not write output.json: %s", exc)
        return

    _print_post_run_summary(final_report)
