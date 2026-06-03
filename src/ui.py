# extracted from main.py
"""Live console interface and rich rendering helpers."""

from __future__ import annotations

import bisect
import contextlib

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table
from rich.text import Text

try:
    from .fusion import FinalReport
    from .engine.analyzer import StructuralAnalysisResult
    from .engine.validator import ValidationResult
    from .engine.security import SecurityReport
    from .engine.diff import DeltaReport
except ImportError:  # pragma: no cover
    from fusion import FinalReport
    from engine.analyzer import StructuralAnalysisResult
    from engine.validator import ValidationResult
    from engine.security import SecurityReport
    from engine.diff import DeltaReport


# ---------------------------------------------------------------------------
# Rich UI Renderer
# ---------------------------------------------------------------------------


class RichUIRenderer:
    """Live console interface.  Fully decoupled from analysis logic.

    Manages a ``rich.live.Live`` session with a Layout containing:
    *   Header panel
    *   Progress bar panel
    *   Main split (function metrics table | security findings table)
    *   Footer panel (unified risk score, colour-coded)
    """

    def __init__(self, file_path: str | None = None, anomaly_threshold: float = 1.5) -> None:
        self.console = Console()
        self.file_path = file_path
        self._anomaly_threshold: float = anomaly_threshold

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(complete_style="green", finished_style="green"),
            TaskProgressColumn(),
        )
        self._tasks: dict[str, TaskID] = {
            "analyzer": self.progress.add_task("[cyan]Analyzer", total=1),
            "security": self.progress.add_task("[magenta]Security", total=1),
            "validator": self.progress.add_task("[yellow]Validator", total=1),
            "fusion": self.progress.add_task("[green]Fusion", total=1),
        }

        self.layout = Layout()
        self._build_layout()

        # Result caches
        self._analysis: StructuralAnalysisResult | None = None
        self._validation: ValidationResult | None = None
        self._security: SecurityReport | None = None
        self._final: FinalReport | None = None

    # -- layout construction -----------------------------------------------

    def _build_layout(self) -> None:
        title = (
            f"Omni-Auditor — {self.file_path}"
            if self.file_path
            else "Omni-Auditor"
        )
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="progress", size=3),
            Layout(name="main"),
            Layout(name="footer", minimum_size=10),
        )
        self.layout["header"].update(
            Panel(Text(title, style="bold magenta", justify="center"))
        )
        self.layout["progress"].update(
            Panel(self.progress, title="[b]Pipeline Progress[/b]")
        )
        self.layout["main"].split_row(
            Layout(name="functions", ratio=1),
            Layout(name="security", ratio=1),
        )
        self.layout["footer"].update(
            Panel(Text("Awaiting fusion ...", style="dim"), title="[b]Risk Assessment[/b]")
        )

    # -- context manager ---------------------------------------------------

    @contextlib.contextmanager
    def live(self):
        """Context manager that yields the renderer while the live display is active."""
        with Live(self.layout, console=self.console, refresh_per_second=4, screen=False):
            yield self

    # -- progress updates --------------------------------------------------

    def update_stage(self, name: str, status: str) -> None:
        task_id = self._tasks.get(name)
        if task_id is None:
            return
        if status == "complete":
            self.progress.update(task_id, completed=1)
        elif status == "running":
            self.progress.update(
                task_id, description=f"[bold]{name.capitalize()}[/bold] [yellow]running[/yellow]"
            )
        elif status == "error":
            self.progress.update(
                task_id, description=f"[bold]{name.capitalize()}[/bold] [red]error[/red]"
            )

    # -- result setters ----------------------------------------------------

    def set_analysis_result(self, result: StructuralAnalysisResult) -> None:
        self._analysis = result
        self._render_functions_table()

    def set_validation_result(self, result: ValidationResult) -> None:
        self._validation = result
        if hasattr(result, "anomaly_threshold"):
            self._anomaly_threshold = result.anomaly_threshold
        self._render_functions_table()

    def set_security_result(self, result: SecurityReport) -> None:
        self._security = result
        self._render_security_table()

    def set_final_report(self, report: FinalReport) -> None:
        self._final = report
        self._render_footer()

    # -- render helpers ----------------------------------------------------

    def _render_functions_table(self) -> None:
        table = Table(
            title="Per-Function Metrics",
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )
        table.add_column("Function", style="cyan", no_wrap=True)
        table.add_column("Line", justify="right")
        table.add_column("Blocks", justify="right")
        table.add_column("Anomaly Z", justify="right")
        table.add_column("Status", justify="center")

        if self._analysis is None:
            self.layout["functions"].update(Panel(table))
            return

        sec_details = self._get_security_details_by_function()
        messages: list[Text] = []

        for key, profile in self._analysis.function_spectrals.items():
            line = self._extract_line(key)
            n_blocks = profile.adjacency_undirected.shape[0]
            func_name = key.split("@")[0]

            z_val: float | None = None
            if self._validation is not None:
                func_rep = self._validation.function_reports.get(key)
                if func_rep is not None:
                    z_val = func_rep.renyi_z_score

            z_score_str = f"{z_val:.2f}" if z_val is not None else "—"
            status = self._status_for_function(func_name, z_val, n_blocks, sec_details)
            table.add_row(func_name, line, str(n_blocks), z_score_str, status)

            msg = self._function_message(func_name, z_val, n_blocks, sec_details)
            if msg is not None:
                messages.append(msg)

        content: Table | Group = table
        if messages:
            content = Group(table, Text(), *messages)

        self.layout["functions"].update(
            Panel(content, title="[b]Spectral & Statistical Analysis[/b]")
        )

    def _render_security_table(self) -> None:
        if self._security is None:
            self.layout["security"].update(
                Panel(Text("Pending ...", style="dim"), title="[b]Security Findings[/b]")
            )
            return

        if not self._security.threats:
            self.layout["security"].update(
                Panel(
                    Text("✓ No threats detected", style="bold green"),
                    title="[b]Security Findings[/b]",
                )
            )
            return

        table = Table(
            show_header=True,
            header_style="bold",
            expand=True,
        )
        table.add_column("Severity", style="bold")
        table.add_column("Line", justify="right")
        table.add_column("Category")
        table.add_column("Confidence", justify="right")

        color_map = {
            "CRITICAL": "bright_red",
            "HIGH": "red",
            "MEDIUM": "yellow",
            "LOW": "green",
        }

        for threat in self._security.threats[:50]:  # cap UI at 50 rows
            color = color_map.get(threat.severity, "white")
            table.add_row(
                f"[{color}]{threat.severity}[/{color}]",
                str(threat.line_number),
                threat.category,
                f"{threat.confidence_score:.2f}",
            )

        remaining = len(self._security.threats) - 50
        if remaining > 0:
            table.add_row(
                "", "", f"[dim]... and {remaining} more[/dim]", ""
            )

        self.layout["security"].update(
            Panel(table, title=f"[b]Security Findings ({len(self._security.threats)})[/b]")
        )

    def _render_footer(self) -> None:
        if self._final is None:
            return

        tier = self._final.risk_tier
        score = self._final.unified_risk_score
        color_map = {
            "LOW": "green",
            "MEDIUM": "yellow",
            "HIGH": "red",
            "CRITICAL": "bright_red",
        }
        color = color_map.get(tier, "white")
        w_a, w_v, w_s = self._final.fusion_weights

        recommendations = self._build_recommendations()

        lines: list[Text | Group] = [
            Text.assemble(
                ("Unified Risk Score: ", "bold"),
                (f"{score:.4f}  ", f"{color} bold"),
                (f"[{tier}]", f"{color} bold underline"),
            ),
        ]

        if recommendations:
            lines.append(Text("Recommendations:", style="bold underline"))
            for i, rec in enumerate(recommendations, 1):
                lines.append(Text(f"{i}. {rec}"))
        else:
            lines.append(Text("🟢 No critical issues detected.", style="bold green"))

        lines.append(
            Text.assemble(
                (f"Fusion weights  ", "dim"),
                (f"Analyzer={w_a:.2f}  ", "cyan"),
                (f"Validator={w_v:.2f}  ", "yellow"),
                (f"Security={w_s:.2f}", "magenta"),
            ),
        )

        self.layout["footer"].update(
            Panel(Group(*lines), title="[b]Final Assessment[/b]", border_style=color)
        )

    @staticmethod
    def _extract_line(key: str) -> str:
        try:
            return key.split("@")[1].split(":")[0]
        except IndexError:
            return "?"

    def _func_line_map(self) -> list[tuple[int, str]]:
        """Return sorted list of (start_line, func_name) for all functions."""
        if self._analysis is None:
            return []
        func_lines: list[tuple[int, str]] = []
        for key in self._analysis.function_spectrals:
            line_str = self._extract_line(key)
            name = key.split("@")[0]
            try:
                func_lines.append((int(line_str), name))
            except ValueError:
                continue
        func_lines.sort(key=lambda x: x[0])
        return func_lines

    def _get_security_details_by_function(self) -> dict[str, dict[str, int]]:
        """Map security threats to function names and return severity breakdowns."""
        if self._security is None or self._analysis is None:
            return {}
        func_lines = self._func_line_map()
        if not func_lines:
            return {}
        lines = [fl[0] for fl in func_lines]
        names = [fl[1] for fl in func_lines]
        details: dict[str, dict[str, int]] = {}
        for threat in self._security.threats:
            idx = bisect.bisect_right(lines, threat.line_number) - 1
            if idx >= 0:
                fname = names[idx]
                details.setdefault(fname, {})
                details[fname][threat.severity] = details[fname].get(threat.severity, 0) + 1
        return details

    def _status_for_function(
        self,
        func_name: str,
        z_score: float | None,
        n_blocks: int,
        sec_details: dict[str, dict[str, int]],
    ) -> Text:
        """Return a rich Text object representing the function status."""
        sev_counts = sec_details.get(func_name, {})
        if sev_counts.get("CRITICAL", 0) > 0 or sev_counts.get("HIGH", 0) > 0:
            return Text("🔴 Security Critical", style="bold red")
        if sum(sev_counts.values()) > 0:
            return Text("⚠️ Fractured", style="bold yellow")
        if z_score is not None and z_score > self._anomaly_threshold and n_blocks > 10:
            return Text("🔴 Critical", style="bold red")
        if (z_score is not None and z_score >= 0.5) or n_blocks > 10:
            return Text("🟡 Warning", style="bold yellow")
        if z_score is None:
            return Text("—", style="dim")
        return Text("🟢 Healthy", style="bold green")

    def _function_message(
        self,
        func_name: str,
        z_score: float | None,
        n_blocks: int,
        sec_details: dict[str, dict[str, int]],
    ) -> Text | None:
        """Return an actionable footer message for a single function, or None."""
        sev_counts = sec_details.get(func_name, {})
        count = sum(sev_counts.values())
        if count > 0:
            critical_count = sev_counts.get("CRITICAL", 0)
            if critical_count > 0:
                plural = "s" if critical_count > 1 else ""
                return Text(
                    f"⚠️ {func_name}: Contains {critical_count} CRITICAL security issue{plural}. Immediate review required.",
                    style="bold red",
                )
            plural = "s" if count > 1 else ""
            return Text(
                f"⚠️ {func_name}: Contains {count} security issue{plural}. Review required.",
                style="bold yellow",
            )
        if z_score is not None and z_score > 1.0 and n_blocks > 10:
            return Text(
                f"🔴 {func_name}: High structural complexity ({n_blocks} blocks). Consider extracting inner loops into helper functions.",
                style="bold red",
            )
        if z_score is not None and z_score < 0.5 and count == 0:
            return Text(
                f"🟢 {func_name} is structurally healthy.",
                style="bold green",
            )
        return None

    def _build_recommendations(self) -> list[str]:
        """Generate top-3 actionable recommendations sorted by priority."""
        if self._final is None:
            return []
        recs: list[tuple[tuple[int, int], str]] = []

        if self._security is not None and self._analysis is not None:
            func_lines = self._func_line_map()
            lines = [fl[0] for fl in func_lines]
            names = [fl[1] for fl in func_lines]
            for threat in self._security.threats:
                idx = bisect.bisect_right(lines, threat.line_number) - 1
                func_name = names[idx] if idx >= 0 else "?"
                if threat.severity == "CRITICAL":
                    text = f"[SECURITY] Remove {threat.node_path} from {func_name} (line {threat.line_number})"
                    recs.append(((0, threat.line_number), text))
                elif threat.severity == "HIGH":
                    text = f"[SECURITY] Review {threat.node_path} from {func_name} (line {threat.line_number})"
                    recs.append(((1, threat.line_number), text))

        if self._analysis is not None and self._validation is not None:
            for key, profile in self._analysis.function_spectrals.items():
                func_name = key.split("@")[0]
                n_blocks = profile.adjacency_undirected.shape[0]
                func_rep = self._validation.function_reports.get(key)
                z_score = func_rep.renyi_z_score if func_rep is not None else None
                if z_score is not None and z_score > 1.0:
                    text = (
                        f"[STRUCTURE] Refactor {func_name}() — {n_blocks} nested blocks, "
                        "consider extracting helper functions"
                    )
                    recs.append(((2, 0), text))
                elif (z_score is not None and z_score >= 0.5) or n_blocks > 10:
                    text = f"[MAINTAINABILITY] Simplify {func_name}() or add input validation"
                    recs.append(((3, 0), text))

        recs.sort(key=lambda x: x[0])
        seen: set[str] = set()
        unique_recs: list[str] = []
        for _, text in recs:
            if text not in seen:
                seen.add(text)
                unique_recs.append(text)
                if len(unique_recs) == 3:
                    break
        return unique_recs


# ---------------------------------------------------------------------------
# Delta report renderer
# ---------------------------------------------------------------------------


def _print_delta_report(delta: DeltaReport) -> None:
    """Render a :class:`DeltaReport` through ``rich`` tables."""
    console = Console()
    console.print()

    trend_colors = {
        "IMPROVED": "green",
        "STABLE": "blue",
        "DEGRADED": "yellow",
        "FRACTURED": "red",
    }
    color = trend_colors.get(delta.risk_trend, "white")
    arrow = {
        "IMPROVED": "v",
        "STABLE": "->",
        "DEGRADED": "^",
        "FRACTURED": "^^",
    }.get(delta.risk_trend, "")

    summary = Table(
        title="Structural Delta Report",
        show_header=True,
        header_style="bold magenta",
    )
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", justify="right")
    summary.add_row("Project ID", delta.project_id)
    summary.add_row("Drift Score", f"{delta.drift_score:.4f}")
    summary.add_row("Risk Trend", f"[{color}]{arrow} {delta.risk_trend}[/{color}]")
    console.print(summary)

    if delta.function_changes:
        func_table = Table(
            title="Function Changes",
            show_header=True,
            header_style="bold cyan",
        )
        func_table.add_column("Function")
        func_table.add_column("Change")
        for fc in delta.function_changes:
            func_table.add_row(fc["function"], fc["change"])
        console.print(func_table)

    metric_table = Table(
        title="Metric Deltas",
        show_header=True,
        header_style="bold yellow",
    )
    metric_table.add_column("Metric")
    metric_table.add_column("Raw")
    metric_table.add_column("Normalized")
    pm = delta.per_metric_deltas
    metric_table.add_row(
        "Laplacian Frobenius",
        f"{pm.get('laplacian_frobenius', 0):.4f}",
        f"{pm.get('normalized_frobenius', 0):.4f}",
    )
    metric_table.add_row(
        "Eigenvalue Drift",
        f"{pm.get('eigenvalue_drift', 0):.4f}",
        f"{pm.get('normalized_eigenvalue_drift', 0):.4f}",
    )
    metric_table.add_row(
        "Fiedler Shift",
        f"{pm.get('fiedler_shift', 0):.4f}",
        f"{pm.get('normalized_fiedler_shift', 0):.4f}",
    )
    metric_table.add_row(
        "Modularity Delta",
        f"{pm.get('modularity_delta', 0):.4f}",
        f"{pm.get('normalized_modularity_delta', 0):.4f}",
    )
    metric_table.add_row(
        "Security Delta",
        str(pm.get("security_delta", 0)),
        f"{pm.get('normalized_security_delta', 0):.4f}",
    )
    console.print(metric_table)


# ---------------------------------------------------------------------------
# Post-run summary
# ---------------------------------------------------------------------------


def _print_post_run_summary(report: FinalReport) -> None:
    """Static post-run summary (after Live context has exited)."""
    console = Console()
    console.print()
    console.print("[bold green]Analysis Complete.[/bold green]")
    console.print(f"Risk Tier    : [bold]{report.risk_tier}[/bold]")
    console.print(f"Unified Score: [bold]{report.unified_risk_score:.4f}[/bold]")
    console.print(
        f"Threats      : {report.security.total_threats} "
        f"({report.security.severity_counts.get('CRITICAL', 0)} critical)"
    )
