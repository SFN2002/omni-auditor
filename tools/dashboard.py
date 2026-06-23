"""
Omni-Auditor Spectral Analysis Dashboard
========================================
Run:      streamlit run tools/dashboard.py
Requires: pip install streamlit plotly pandas
Data:     Upload a JSON report from: omni-auditor file.py --json
          Or pre-load a report: streamlit run tools/dashboard.py -- --report report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


class DashboardApp:
    """Production-grade Streamlit dashboard for Omni-Auditor spectral reports."""

    # ------------------------------------------------------------------
    # Constants
    # ------------------------------------------------------------------
    TIER_COLORS: dict[str, str] = {
        "CRITICAL": "#ef4444",
        "HIGH": "#f97316",
        "MEDIUM": "#facc15",
        "LOW": "#22c55e",
        "UNKNOWN": "#94a3b8",
    }

    REQUIRED_KEYS: tuple[str, ...] = (
        "unified_risk_score",
        "risk_tier",
        "fusion_weights",
        "per_function_metrics",
        "security_findings",
    )

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.df_functions: pd.DataFrame = pd.DataFrame()
        self.df_security: pd.DataFrame = pd.DataFrame()

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def _inject_custom_css(self) -> None:
        """Inject dark-mode friendly SaaS styling."""
        st.markdown(
            """
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

                html, body, [class*="css"] {
                    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
                }

                [data-testid="stAppViewContainer"] {
                    background-color: #0b0c15;
                }

                [data-testid="stSidebar"] {
                    background-color: #11121e;
                    border-right: 1px solid rgba(255,255,255,0.06);
                }

                .block-container {
                    padding-top: 2rem;
                    padding-bottom: 3rem;
                    max-width: 1400px;
                }

                /* KPI Cards */
                .kpi-card {
                    background: linear-gradient(145deg, #161827 0%, #1e2035 100%);
                    border-radius: 16px;
                    padding: 28px 20px;
                    border: 1px solid rgba(255,255,255,0.06);
                    box-shadow: 0 8px 32px rgba(0,0,0,0.35);
                    text-align: center;
                    transition: transform 0.25s ease, box-shadow 0.25s ease;
                }
                .kpi-card:hover {
                    transform: translateY(-3px);
                    box-shadow: 0 12px 40px rgba(0,0,0,0.45);
                }
                .kpi-value {
                    font-size: 3.2rem;
                    font-weight: 800;
                    margin: 0;
                    line-height: 1.05;
                    letter-spacing: -1px;
                }
                .kpi-label {
                    font-size: 0.8rem;
                    font-weight: 600;
                    text-transform: uppercase;
                    letter-spacing: 2px;
                    color: #7a7d9c;
                    margin-top: 10px;
                }
                .tier-badge {
                    display: inline-block;
                    padding: 8px 20px;
                    border-radius: 999px;
                    font-weight: 800;
                    font-size: 0.9rem;
                    letter-spacing: 1.5px;
                    text-transform: uppercase;
                    border: 1px solid currentColor;
                }

                /* Tables */
                .security-table {
                    width: 100%;
                    border-collapse: collapse;
                    font-size: 0.9rem;
                }
                .security-table th {
                    background-color: #1e2035 !important;
                    color: #e2e4f0 !important;
                    padding: 14px 16px;
                    text-align: left;
                    font-weight: 700;
                    text-transform: uppercase;
                    font-size: 0.75rem;
                    letter-spacing: 1px;
                    border-bottom: 2px solid rgba(255,255,255,0.08);
                }
                .security-table td {
                    padding: 12px 16px;
                    border-bottom: 1px solid rgba(255,255,255,0.04);
                    color: #c9cce0;
                }
                .security-table tr:hover td {
                    background-color: rgba(255,255,255,0.02);
                }

                /* Streamlit tab styling */
                .stTabs [data-baseweb="tab-list"] {
                    gap: 8px;
                    border-bottom: 1px solid rgba(255,255,255,0.08);
                }
                .stTabs [data-baseweb="tab"] {
                    background-color: transparent;
                    border-radius: 8px 8px 0 0;
                    padding: 10px 20px;
                    font-weight: 600;
                    color: #7a7d9c;
                }
                .stTabs [aria-selected="true"] {
                    background-color: rgba(255,255,255,0.04) !important;
                    color: #e2e4f0 !important;
                    border-bottom: 2px solid #6366f1;
                }

                /* Metric dims */
                .dim-metric [data-testid="stMetricValue"] {
                    font-size: 0.95rem !important;
                    font-family: 'SF Mono', monospace !important;
                }
                .dim-metric [data-testid="stMetricLabel"] {
                    font-size: 0.7rem !important;
                    color: #7a7d9c !important;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )

    # ------------------------------------------------------------------
    # Data ingestion & validation
    # ------------------------------------------------------------------
    def _validate_json(self, payload: dict[str, Any]) -> bool:
        """Ensure all required top-level keys are present."""
        missing = [k for k in self.REQUIRED_KEYS if k not in payload]
        if missing:
            st.error(f"Missing required JSON keys: {', '.join(f'`{k}`' for k in missing)}")
            return False
        return True

    def load_data(self, uploaded_file: Any) -> bool:
        """Parse uploaded JSON and build internal DataFrames."""
        try:
            raw: dict[str, Any] = json.load(uploaded_file)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON file: {exc}")
            return False
        except Exception as exc:  # pragma: no cover
            st.error(f"Unexpected error reading file: {exc}")
            return False

        if not self._validate_json(raw):
            return False

        self.data = raw

        # Functions DataFrame
        funcs = raw.get("per_function_metrics", [])
        if funcs:
            self.df_functions = pd.DataFrame(funcs)
            self.df_functions["function_name"] = (
                self.df_functions["function_key"].str.split("@").str[0]
            )
        else:
            self.df_functions = pd.DataFrame()

        # Security DataFrame
        sec = raw.get("security_findings", [])
        self.df_security = pd.DataFrame(sec) if sec else pd.DataFrame()

        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _tier_color(self, tier: str) -> str:
        return self.TIER_COLORS.get(tier.upper(), self.TIER_COLORS["UNKNOWN"])

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    def render_header(self) -> None:
        """Top-level header with branding."""
        col_icon, col_text = st.columns([0.08, 0.92])
        with col_icon:
            st.markdown("<h1 style='font-size: 2.8rem; margin: 0;'>🛡️</h1>", unsafe_allow_html=True)
        with col_text:
            st.markdown(
                """
                <h1 style='margin-bottom: 0; font-size: 1.9rem; letter-spacing: -0.5px;'>Omni-Auditor</h1>
                <p style='color: #7a7d9c; margin-top: 4px; font-size: 0.95rem;'>
                    Spectral Graph Analysis & Structural Anomaly Intelligence
                </p>
                """,
                unsafe_allow_html=True,
            )
        st.divider()

    def render_hero(self) -> None:
        """KPI cards for risk score, tier, and fusion weights."""
        score = float(self.data.get("unified_risk_score", 0.0))
        tier = str(self.data.get("risk_tier", "UNKNOWN"))
        weights = self.data.get("fusion_weights", [0.0, 0.0, 0.0])
        color = self._tier_color(tier)

        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-value" style="color: {color};">{score:.4f}</div>
                    <div class="kpi-label">Unified Risk Score</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with c2:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div style="padding-top: 10px;">
                        <span class="tier-badge" style="color: {color}; border-color: {color}55;">
                            {tier}
                        </span>
                    </div>
                    <div class="kpi-label">Risk Tier</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with c3:
            w_a, w_v, w_s = weights
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div style="font-size: 0.8rem; color: #7a7d9c; margin-bottom: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">
                        Fusion Weights
                    </div>
                    <div style="display: flex; justify-content: space-around; font-size: 1rem; font-weight: 700;">
                        <div><span style="color: #38bdf8;">A</span>&nbsp;{w_a:.2f}</div>
                        <div><span style="color: #fbbf24;">V</span>&nbsp;{w_v:.2f}</div>
                        <div><span style="color: #c084fc;">S</span>&nbsp;{w_s:.2f}</div>
                    </div>
                    <div style="font-size: 0.7rem; color: #555; margin-top: 8px; letter-spacing: 0.5px;">
                        Analyzer · Validator · Security
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)

    def render_vector_analysis(self) -> None:
        """Plotly charts for structural anomaly vectors."""
        st.subheader("📊 Structural Anomaly Signatures")

        if self.df_functions.empty:
            st.info("No per-function metrics available in this report.")
            return

        col_bar, col_scatter = st.columns(2)

        with col_bar:
            st.caption("Anomaly Score by Function")
            fig_bar = px.bar(
                self.df_functions,
                x="function_name",
                y="anomaly_score",
                color="anomaly_score",
                color_continuous_scale=["#22c55e", "#facc15", "#ef4444"],
                range_color=[0, max(2.0, self.df_functions["anomaly_score"].max())],
                template="plotly_dark",
                text_auto=".2f",
            )
            fig_bar.update_traces(textfont_size=12, textposition="outside", cliponaxis=False)
            fig_bar.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=20, r=20, t=40, b=20),
                xaxis_title=None,
                yaxis_title="Anomaly Score",
                showlegend=False,
                height=380,
            )
            st.plotly_chart(fig_bar, use_container_width=True, key="bar_anomaly")

        with col_scatter:
            st.caption("Mahalanobis Distance vs. Rényi Z-Score")
            fig_scatter = px.scatter(
                self.df_functions,
                x="mahalanobis_distance",
                y="renyi_z_score",
                size="anomaly_score",
                size_max=25,
                color="anomaly_score",
                hover_name="function_name",
                hover_data={"anomaly_score": ":.3f", "mahalanobis_distance": ":.3f", "renyi_z_score": ":.3f"},
                color_continuous_scale=["#22c55e", "#facc15", "#ef4444"],
                template="plotly_dark",
            )
            fig_scatter.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=20, r=20, t=40, b=20),
                xaxis_title="Mahalanobis Distance",
                yaxis_title="Rényi Z-Score",
                showlegend=False,
                height=380,
            )
            st.plotly_chart(fig_scatter, use_container_width=True, key="scatter_mv")

    def render_security_intelligence(self) -> None:
        """Searchable, filterable security findings table."""
        st.subheader("🔐 Security Intelligence")

        if self.df_security.empty:
            st.info("No security findings detected.")
            return

        # Filters
        f1, f2, f3 = st.columns(3)
        with f1:
            severities = st.multiselect(
                "Severity",
                options=sorted(self.df_security["severity"].unique()),
                default=sorted(self.df_security["severity"].unique()),
            )
        with f2:
            categories = st.multiselect(
                "Category",
                options=sorted(self.df_security["category"].unique()),
                default=sorted(self.df_security["category"].unique()),
            )
        with f3:
            search = st.text_input("Search node_path", placeholder="e.g. eval, os.system, pickle...")

        filtered = self.df_security[
            self.df_security["severity"].isin(severities)
            & self.df_security["category"].isin(categories)
        ]
        if search.strip():
            filtered = filtered[
                filtered["node_path"].str.contains(search, case=False, na=False)
            ]

        st.caption(f"Displaying **{len(filtered)}** of {len(self.df_security)} findings")

        # Styled HTML table with severity highlighting
        display = filtered.copy()
        display["confidence_score"] = display["confidence_score"].apply(lambda x: f"{x:.2f}")
        display = display[["severity", "category", "line_number", "node_path", "confidence_score"]]

        def _highlight_rows(row: pd.Series) -> list[str]:
            sev = row.get("severity", "").upper()
            if sev == "CRITICAL":
                return ["background-color: #ef444415; color: #ef4444; font-weight: 700"] * len(row)
            if sev == "HIGH":
                return ["background-color: #f9731615; color: #f97316; font-weight: 600"] * len(row)
            if sev == "MEDIUM":
                return ["background-color: #facc1515; color: #eab308"] * len(row)
            return [""] * len(row)

        styled_html = (
            display.style.apply(_highlight_rows, axis=1)
            .hide(axis="index")
            .to_html(classes="security-table")
        )
        st.write(styled_html, unsafe_allow_html=True)

    def render_raw_data_inspector(self) -> None:
        """Expandable 56-D raw feature vector viewer."""
        st.subheader("🔬 Raw Feature Vector Inspector")

        if self.df_functions.empty:
            st.info("No function data available.")
            return

        selected_key = st.selectbox(
            "Select Function",
            options=self.df_functions["function_key"].tolist(),
            format_func=lambda x: x.split("@")[0],
        )

        row = self.df_functions[self.df_functions["function_key"] == selected_key].iloc[0]
        vec = row.get("raw_feature_vector", [])

        with st.expander("View 56-D Spectral Feature Vector", expanded=True):
            if not vec:
                st.warning("No `raw_feature_vector` present for this function.")
                return

            # Grid of metric cards
            n_dims = len(vec)
            cols_per_row = 7
            rows = (n_dims + cols_per_row - 1) // cols_per_row

            for r in range(rows):
                cols = st.columns(cols_per_row)
                for c in range(cols_per_row):
                    idx = r * cols_per_row + c
                    if idx < n_dims:
                        with cols[c]:
                            st.metric(
                                label=f"Dim {idx}",
                                value=f"{vec[idx]:.4f}",
                            )

            st.divider()

            # ── Primary: 56-D Horizontal Bar Heatmap ─────────────────────
            st.caption("56-D Feature Heatmap (blue → red)")
            min_val, max_val = float(min(vec)), float(max(vec))
            rng = max_val - min_val if max_val != min_val else 1.0

            bar_colors = []
            for v in vec:
                t = (v - min_val) / rng
                r = int(59 + t * (239 - 59))
                g = int(130 + t * (68 - 130))
                b = int(246 + t * (68 - 246))
                bar_colors.append(f"rgb({r},{g},{b})")

            groups = ["Module"] * 14 + ["Mean"] * 14 + ["Max"] * 14 + ["Std"] * 14

            fig_hm = go.Figure()
            fig_hm.add_trace(
                go.Bar(
                    x=list(vec),
                    y=list(range(n_dims)),
                    orientation="h",
                    marker_color=bar_colors,
                    customdata=[[i, groups[i]] for i in range(n_dims)],
                    hovertemplate="Dimension %{customdata[0]} (%{customdata[1]}): %{x:.4f}<extra></extra>",
                )
            )
            for boundary in [13.5, 27.5, 41.5]:
                fig_hm.add_hline(
                    y=boundary,
                    line_dash="dot",
                    line_color="rgba(255,255,255,0.15)",
                )
            fig_hm.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=80, r=20, t=30, b=20),
                height=600,
                xaxis_title="Feature Value",
                yaxis=dict(
                    tickmode="array",
                    tickvals=[6.5, 20.5, 34.5, 48.5],
                    ticktext=["Module", "Mean", "Max", "Std"],
                    showgrid=False,
                ),
            )
            st.plotly_chart(fig_hm, use_container_width=True, key="vector_heatmap")

            # ── Secondary: Sparkline ─────────────────────────────────────
            st.caption("Feature Sparkline (secondary)")
            fig_spark = go.Figure()
            fig_spark.add_trace(
                go.Scatter(
                    y=list(vec),
                    mode="lines+markers",
                    line=dict(color="#6366f1", width=2),
                    marker=dict(size=4, color="#818cf8"),
                    fill="tozeroy",
                    fillcolor="rgba(99,102,241,0.06)",
                    name="Feature Value",
                )
            )
            fig_spark.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=20, r=20, t=20, b=20),
                xaxis_title="Dimension Index",
                yaxis_title="Value",
                height=240,
            )
            st.plotly_chart(fig_spark, use_container_width=True, key="vector_sparkline")

    def render_ai_interpretation(self) -> None:
        """Gemini prompt builder + placeholder for LLM response."""
        st.subheader("🤖 AI Structural Interpretation")

        st.markdown(
            "Generate a natural-language explanation of the spectral anomalies, "
            "security posture, and recommended remediation steps."
        )

        if st.button("Explain with Gemini", type="primary", use_container_width=True):
            prompt = self._build_gemini_prompt()

            with st.container(border=True):
                st.caption("📤 Prompt sent to Gemini")
                st.code(prompt, language="markdown")

            st.divider()

            st.info("⏳ AI response placeholder — integrate `google.generativeai` to stream real responses.")
            st.markdown(
                """
                <div style="background: #131525; border-radius: 14px; padding: 24px; border: 1px dashed #2d2f45;">
                    <p style="color: #6b6d85; font-style: italic; margin: 0; line-height: 1.6;">
                        In production, wire this button to:<br>
                        <code style="color: #a5b4fc;">
                            genai.GenerativeModel('gemini-pro').generate_content(prompt)
                        </code>
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    def _build_gemini_prompt(self) -> str:
        """Format the loaded report into a clean markdown prompt for Gemini."""
        score = float(self.data.get("unified_risk_score", 0.0))
        tier = str(self.data.get("risk_tier", "UNKNOWN"))
        funcs = self.data.get("per_function_metrics", [])
        threats = self.data.get("security_findings", [])

        lines = [
            "# Omni-Auditor Spectral Analysis Report",
            f"**Unified Risk Score:** {score:.4f}",
            f"**Risk Tier:** {tier}",
            "",
            "## Per-Function Structural Anomalies",
        ]
        for f in funcs:
            lines.append(
                f"- `{f['function_key']}` → "
                f"Mahalanobis={f['mahalanobis_distance']:.3f}, "
                f"Rényi Z={f['renyi_z_score']:.3f}, "
                f"Anomaly={f['anomaly_score']:.3f}"
            )
        lines.append("")
        lines.append("## Security Findings")
        for t in threats:
            lines.append(
                f"- **[{t['severity']}]** `{t['category']}` at line {t['line_number']} "
                f"(`{t['node_path']}`, confidence={t['confidence_score']:.2f})"
            )
        lines.append("")
        lines.append(
            "## Task\n"
            "Explain the structural fragility of this codebase in 3 paragraphs. "
            "Highlight the single most critical function, interpret why its spectral signature is anomalous, "
            "and suggest concrete refactoring or remediation steps."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Entry point — configures Streamlit and renders the full dashboard."""
        st.set_page_config(
            page_title="Omni-Auditor | Spectral Dashboard",
            page_icon="🛡️",
            layout="wide",
            initial_sidebar_state="expanded",
        )
        self._inject_custom_css()
        self.render_header()

        # ── Sidebar Branding --------------------------------------------
        placeholder = np.full((100, 100, 3), 22, dtype=np.uint8)
        st.sidebar.image(placeholder, caption="🛡️ Omni-Auditor v0.1.0", use_container_width=True)
        st.sidebar.markdown("[Dashboard](#) · [Documentation](#) · [GitHub](#)")
        st.sidebar.divider()
        st.sidebar.caption("Built with Spectral Graph Theory + Rényi Entropy")

        # ── Sidebar -----------------------------------------------------
        with st.sidebar:
            st.markdown("### 📁 Data Ingestion")
            uploaded = st.file_uploader(
                "Upload Omni-Auditor JSON",
                type=["json"],
                help="Upload the output from: python -m src.main file.py --json",
            )

            if uploaded is not None:
                success = self.load_data(uploaded)
                if success:
                    st.success("JSON loaded successfully")
                    st.divider()
                    st.markdown("#### Quick Stats")
                    c1, c2 = st.columns(2)
                    c1.metric("Functions", len(self.df_functions))
                    c2.metric("Threats", len(self.df_security))
            else:
                st.info("Upload a JSON report to begin analysis.")

        # ── Empty state -------------------------------------------------
        if not self.data:
            st.markdown(
                """
                <div style="text-align: center; margin-top: 14vh; color: #44465c;">
                    <div style="font-size: 4rem; margin-bottom: 16px;">📂</div>
                    <h2 style="color: #6b6d85; font-weight: 600;">Waiting for data...</h2>
                    <p style="max-width: 480px; margin: 0 auto; line-height: 1.6;">
                        Upload a JSON analysis report via the sidebar to visualize
                        spectral vectors, security findings, and structural anomalies.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            return

        # ── Main dashboard ----------------------------------------------
        self.render_hero()

        tab_vectors, tab_security, tab_raw, tab_ai = st.tabs(
            ["📊  Vector Analysis", "🔐  Security Intelligence", "🔬  Raw Inspector", "🤖  AI Interpretation"]
        )

        with tab_vectors:
            self.render_vector_analysis()
        with tab_security:
            self.render_security_intelligence()
        with tab_raw:
            self.render_raw_data_inspector()
        with tab_ai:
            self.render_ai_interpretation()


def _parse_cli_args(argv: list[str]) -> argparse.Namespace:
    """Parse optional CLI arguments, tolerating Streamlit's own flags."""
    parser = argparse.ArgumentParser(
        prog="dashboard",
        description="Streamlit dashboard for Omni-Auditor JSON reports.",
    )
    parser.add_argument(
        "--report",
        "-r",
        type=Path,
        default=None,
        help="Optional path to a pre-generated Omni-Auditor JSON report.",
    )
    # Streamlit passes extra flags; ignore unknown ones.
    args, _ = parser.parse_known_args(argv[1:])
    return args


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    cli_args = _parse_cli_args(sys.argv)
    app = DashboardApp()
    if cli_args.report is not None:
        try:
            with open(cli_args.report, "r", encoding="utf-8") as f:
                app.load_data(f)
        except Exception as exc:  # pragma: no cover
            st.error(f"Could not load report {cli_args.report}: {exc}")
    app.run()
