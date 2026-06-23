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
    from .engine.validator import (
        ModuleAnomalyReport,
        PopulationValidator,
        StatisticalValidator,
        ValidationResult,
    )
    from .engine.security import SafetyGuard, SecurityReport
    from .engine.baseline import BaselineManager, build_spectral_snapshot
    from .engine.diff import SpectralDiffEngine, DeltaReport
    from .reporting.json_exporter import JSONExporter
    from .fusion import FusionEngine, FinalReport
    from .ui import RichUIRenderer, _print_delta_report, _print_post_run_summary
except ImportError:  # pragma: no cover
    from engine.analyzer import Analyzer, StructuralAnalysisResult
    from engine.validator import (
        ModuleAnomalyReport,
        PopulationValidator,
        StatisticalValidator,
        ValidationResult,
    )
    from engine.security import SafetyGuard, SecurityReport
    from engine.baseline import BaselineManager, build_spectral_snapshot
    from engine.diff import SpectralDiffEngine, DeltaReport
    from reporting.json_exporter import JSONExporter
    from fusion import FusionEngine, FinalReport
    from ui import RichUIRenderer, _print_delta_report, _print_post_run_summary

logger = logging.getLogger("omni_auditor")


def _tier_exit_code(tier: str) -> int:
    """Map a risk tier to a process exit code for CI/CLI scripting.

    * LOW / MEDIUM → 0 (success)
    * HIGH         → 1 (soft failure)
    * CRITICAL     → 2 (hard failure)
    """
    if tier == "CRITICAL":
        return 2
    if tier == "HIGH":
        return 1
    return 0


def _exit_code_note(tier: str, code: int) -> str:
    """Return a human-readable note explaining the process exit code."""
    meaning = {
        0: "success",
        1: "HIGH-risk findings detected",
        2: "CRITICAL-risk findings detected",
    }.get(code, "unknown")
    return f"Exiting with code {code} ({meaning}) for risk tier {tier}."


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

    def __init__(
        self,
        source_code: str,
        anomaly_threshold: float = 1.5,
        skip_validator: bool | None = None,
        population_dir: str | Path = ".omni_cache/population",
    ) -> None:
        self.source_code: str = source_code
        self.anomaly_threshold: float = anomaly_threshold
        self.population_dir: Path = Path(population_dir)

        # Auto-detect whether a statistical population is available.  When
        # *skip_validator* is left as None we treat a missing or too-small
        # population as an intentional security-only run rather than a failure.
        if skip_validator is True:
            self.skip_validator: bool = True
        elif skip_validator is False:
            self.skip_validator = False
        else:
            population_path = self.population_dir
            population_files = (
                list(population_path.rglob("*.py")) if population_path.exists() else []
            )
            self.skip_validator = len(population_files) <= 50

    # -- internal CPU-bound runners ----------------------------------------

    def _run_analyzer(self) -> StructuralAnalysisResult:
        return Analyzer(self.source_code).analyze()

    def _run_validator(self, analysis: StructuralAnalysisResult) -> ValidationResult:
        empty_module = ModuleAnomalyReport(
            mahalanobis_distance=0.0,
            renyi_entropy_discrete=0.0,
            renyi_entropy_differential=0.0,
            renyi_z_score=0.0,
            anomaly_score=0.0,
        )
        empty_result = ValidationResult(
            module_report=empty_module,
            function_reports={},
            aggregate_anomaly_vector=np.zeros(16, dtype=np.float64),
            population_feature_matrix=np.zeros((0, 0), dtype=np.float64),
            population_keys=[],
            covariance_matrix=np.zeros((0, 0), dtype=np.float64),
            precision_matrix=np.zeros((0, 0), dtype=np.float64),
            anomaly_threshold=self.anomaly_threshold,
        )
        if self.skip_validator:
            logger.info(
                "Spectral validator disabled; running in security-only mode."
            )
            return empty_result

        # Prefer real population-based validation when a large enough population
        # directory is available.  The missing/too-small case is handled up front
        # by auto-detecting skip_validator, so this fallback is for unexpected
        # errors only (e.g., cache corruption, removed files between init and run).
        try:
            validator = PopulationValidator(
                population_dir=self.population_dir,
                min_population_size=50,
            )
            return validator.validate(analysis, anomaly_threshold=self.anomaly_threshold)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning(
                "Population validation unavailable due to unexpected issue: %s. "
                "Falling back to security-only mode.",
                exc,
            )
            return empty_result

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
        population_dir: str | Path = ".omni_cache/population",
    ) -> None:
        self.source_code: str = source_code
        self.file_path: str | None = file_path
        self.no_ui: bool = no_ui
        self.critical_threshold: float = critical_threshold

        # The statistical validator needs a diverse population of files, not a
        # single-file spectral snapshot (which is what baselines are for).
        population_path = Path(population_dir)
        population_files = list(population_path.rglob("*.py")) if population_path.exists() else []
        has_population = len(population_files) > 50
        skip_validator = not has_population
        if skip_validator:
            if population_path.exists() and len(population_files) > 0:
                logger.info(
                    "Population directory %s contains only %d Python files (minimum 50). "
                    "Spectral validator disabled by design; running in security-only mode. "
                    "Provide a larger --population DIR for structural anomaly detection.",
                    population_path,
                    len(population_files),
                )
            else:
                logger.info(
                    "Running in security-only mode. "
                    "Provide --population DIR for structural anomaly detection."
                )

        self.pipeline = AnalysisPipeline(
            source_code,
            anomaly_threshold=anomaly_threshold,
            skip_validator=skip_validator,
            population_dir=population_path,
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
                    "Spectral validator is disabled by design because no statistical "
                    "population is configured or the provided directory is too small.\n"
                    "Provide --population DIR with > 50 Python files for structural anomaly detection.\n"
                    "Running in security-only mode.",
                    title="[blue]Info[/blue]",
                    border_style="blue",
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
        "--quiet",
        action="store_true",
        help="Suppress all Rich output. Emit a minimal one-line summary unless --json is used.",
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
    parser.add_argument(
        "--population",
        type=str,
        default=None,
        help="Directory of Python files used as a statistical population for structural anomaly detection.",
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
        no_ui=args.json or args.quiet,
        critical_threshold=args.threshold,
        anomaly_threshold=args.anomaly_threshold,
        population_dir=args.population if args.population else ".omni_cache/population",
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
        if not args.json and not args.quiet:
            console = Console()
            console.print(
                f"[bold green]Baseline saved for project '[/bold green]"
                f"[bold cyan]{args.save_baseline}[/bold cyan]"
                f"[bold green]'.[/bold green]"
            )

    delta_report: DeltaReport | None = None
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
            exit_code = _tier_exit_code(final_report.risk_tier)
            payload = {
                "file_path": str(source_path.resolve()),
                "unified_risk_score": final_report.unified_risk_score,
                "risk_tier": final_report.risk_tier,
                "exit_code": exit_code,
                "exit_code_note": _exit_code_note(final_report.risk_tier, exit_code),
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
            sys.exit(exit_code)

        if not args.quiet:
            _print_delta_report(delta_report)
            # Fall through to the normal post-run summary so the user sees both.

    if args.verbose and not args.quiet:
        _print_verbose_metrics(final_report)

    if args.json:
        exporter = JSONExporter()
        exit_code = _tier_exit_code(final_report.risk_tier)
        payload = {
            "file_path": str(source_path.resolve()),
            "unified_risk_score": final_report.unified_risk_score,
            "risk_tier": final_report.risk_tier,
            "exit_code": exit_code,
            "exit_code_note": _exit_code_note(final_report.risk_tier, exit_code),
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
        sys.exit(exit_code)

    if args.quiet:
        exit_code = _tier_exit_code(final_report.risk_tier)
        print(
            f"{source_path}: {final_report.risk_tier} "
            f"(score: {final_report.unified_risk_score:.2f}) "
            f"{_exit_code_note(final_report.risk_tier, exit_code)}"
        )
        sys.exit(exit_code)

    exit_code = _tier_exit_code(final_report.risk_tier)
    _print_post_run_summary(final_report, exit_code=exit_code)
    sys.exit(exit_code)
