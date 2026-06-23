# Omni-Auditor

> Research-grade static analysis engine combining Spectral Graph Theory, Rényi Entropy, and Mahalanobis Distance to detect deep structural fragility in Python code.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Code style: strict typing](https://img.shields.io/badge/code%20style-strict%20typing-blue)
![CI](https://github.com/SFN2002/omni-auditor/actions/workflows/ci.yml/badge.svg)

---

## Overview

Omni-Auditor is a research-grade static analysis engine for Python that goes beyond traditional linting. It converts your code's control flow into spectral graph representations, applies multivariate statistical anomaly detection, and scans for security vulnerabilities — fusing all signals into a single unified risk score.

Built for **security engineers**, **Python developers**, and **DevSecOps teams** who need rigorous, quantitative insight into code health and security posture.

---

## Installation

```bash
pip install omni-auditor
```

Requirements: Python ≥3.10, `numpy`, `scipy`, `rich`.

For the GitHub App server:
```bash
pip install -r github-app/requirements.txt
```

For VS Code extension development:
```bash
cd vscode-extension && npm install
```

---

## Quick Start

Analyze a Python file with the live Rich UI:

```bash
python -m src.main test_sample.py
```

Emit a compact JSON report:

```bash
python -m src.main test_sample.py --json
```

Save a spectral baseline for drift detection:

```bash
python -m src.main test_sample.py --save-baseline my-project
```

Compare against a saved baseline:

```bash
python -m src.main test_sample.py --diff my-project
```

---

## Architecture

Omni-Auditor is built around four engine subsystems orchestrated by an async pipeline in `src/main.py`:

| Engine | File | Purpose |
|--------|------|---------|
| **Analyzer** | `src/engine/analyzer.py` | Parses AST → Control Flow Graphs → Spectral Graph Theory (Laplacians, eigen-decomposition, entropy, modularity). Produces a 56-D structural feature vector. |
| **Validator** | `src/engine/validator.py` | Multivariate statistical anomaly detection: Mahalanobis distance, Rényi-2 entropy (discrete & differential), z-score fusion. Produces a 16-D anomaly vector. |
| **Security** | `src/engine/security.py` | AST-based vulnerability scanning: SQL injection, path traversal, hardcoded secrets, dangerous calls, unsafe deserialization, dynamic execution. Produces an 18-D threat vector. |
| **Diff** | `src/engine/diff.py` | Spectral drift detection between baselines: Laplacian Frobenius distance, eigenvalue KL drift, Fiedler vector shift, modularity delta, security delta. |

The **FusionEngine** adaptively weights the active vectors based on security severity, then computes a unified risk score in `[0, 1]` with tier `LOW | MEDIUM | HIGH | CRITICAL`. In security-only mode (the default when no `--population` is supplied), the 16-D anomaly vector is dropped and the score is computed from the structural and security vectors only.

---

## CLI Flags

| Flag | Description |
|------|-------------|
| `file` | Path to the Python source file to analyse (required). |
| `--json` | Skip the Rich UI and emit a compact JSON report to stdout. |
| `--quiet` | Suppress all Rich output; emit a minimal one-line summary (or JSON when combined with `--json`). |
| `--verbose` | Print detailed per-function spectral metrics before the final report. |
| `--threshold FLOAT` | Override the CRITICAL risk tier threshold (default: `0.7`). |
| `--save-baseline ID` | Persist the current analysis snapshot as a baseline under the given project ID. |
| `--diff ID` | Load a saved baseline and compute structural drift against the current file. |
| `--anomaly-threshold FLOAT` | Z-score threshold for flagging structural anomalies (default: `1.5`). |
| `--population DIR` | Directory of Python files for statistical population-based anomaly detection (requires > 50 files). |

### Exit codes

The CLI returns a process exit code based on the highest risk tier detected. This makes it easy to use Omni-Auditor in CI/CD pipelines:

| Exit code | Meaning | When it happens |
|-----------|---------|-----------------|
| `0` | Success | Risk tier is `LOW` or `MEDIUM`. No action required. |
| `1` | Soft failure | Risk tier is `HIGH`. Review recommended. |
| `2` | Hard failure | Risk tier is `CRITICAL`. Immediate review required. |

In `--quiet` and `--json` modes, and in the Rich post-run summary, the exit code is printed alongside a short explanation so it is never a surprise.

### Security-only mode

By default, Omni-Auditor runs in **security-only mode**: the security scanner and structural analyser are always active, but the statistical validator is skipped unless `--population` is provided with a directory containing more than 50 Python files. This is intentional and eliminates cold-start false positives from an under-sampled population. When this happens you will see an informational message, not an error.

---

## VS Code Extension

Omni-Auditor includes a full VS Code extension for inline analysis.

### Features
- **Inline diagnostics** — Severity-mapped diagnostics in the Problems panel
- **Hover providers** — Module-level and per-function risk metrics on hover
- **Gutter decorations** — Colour-coded severity dots and background highlighting
- **Risk Dashboard** — Webview panel with Chart.js gauges, fusion weights, and findings table
- **Auto-analyse on save** — Optional background analysis every time you save a Python file

### Install
```bash
cd vscode-extension && npm run compile
```

### Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `omniAuditor.pythonPath` | `python` | Path to the Python 3.10+ executable |
| `omniAuditor.projectRoot` | `""` | Absolute path to the Omni-Auditor project root |
| `omniAuditor.threshold` | `0.7` | CRITICAL risk tier threshold |
| `omniAuditor.autoAnalyzeOnSave` | `true` | Run analysis on every save |

---

## GitHub App

The GitHub App provides automated PR analysis with webhook-driven architecture.

### Features
- **Webhook handler** — FastAPI server listening for `pull_request` events
- **Changed-file discovery** — Automatically analyses only modified `.py` files
- **PR comment upserts** — Markdown summary with risk scores and drift indicators
- **Async I/O** — All PyGithub network calls run in thread pools to avoid blocking

### Run locally
```bash
uvicorn github-app.main:app --host 0.0.0.0 --port 8000
```

### Environment variables
| Variable | Description |
|----------|-------------|
| `OMNI_AUDITOR_APP_ID` | GitHub App ID |
| `OMNI_AUDITOR_PRIVATE_KEY` | Path to PEM file or raw PEM string |
| `OMNI_AUDITOR_WEBHOOK_SECRET` | HMAC-SHA256 webhook secret |
| `OMNI_AUDITOR_THRESHOLD` | Risk tier threshold (default: `0.7`) |

---

## GitHub Action

Add Omni-Auditor to your CI pipeline and surface findings in the **GitHub Security tab** via SARIF.

```yaml
name: Omni-Auditor Scan

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  audit:
    runs-on: ubuntu-latest
    permissions:
      security-events: write
    steps:
      - uses: actions/checkout@v4

      - name: Run Omni-Auditor
        id: omni-auditor
        uses: SFN2002/omni-auditor@main
        with:
          path: "."
          threshold: "0.7"

      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: ${{ steps.omni-auditor.outputs.sarif-path }}
```

| Input | Default | Description |
|-------|---------|-------------|
| `path` | `.` | Python file or directory to scan. |
| `threshold` | `0.7` | CRITICAL risk tier threshold. |
| `diff-baseline` | `""` | Optional baseline project ID for drift detection. |

| Output | Description |
|--------|-------------|
| `sarif-path` | Absolute path to the generated SARIF file. |
| `risk-tier` | Highest risk tier observed (`LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`). |
| `finding-count` | Total number of SARIF results produced. |
| `critical-count` | Number of CRITICAL severity findings. |
| `high-count` | Number of HIGH severity findings. |

The action also prints a concise summary in the workflow logs and emits `::notice::`, `::warning::`, or `::error::` annotations based on the highest risk tier.

---

## SARIF Export

Omni-Auditor natively exports **SARIF v2.1.0** for integration with GitHub Advanced Security, VS Code SARIF viewers, and other compatible platforms.

- **Security findings** are mapped to SARIF `results` with accurate severity levels (`error`, `warning`, `note`), line locations, and rule IDs.
- **Structural anomalies** (high Anomaly Z-score) are emitted as `warning`-level results under rule ID `structural-anomaly`.

```python
from src.main import OmniAuditor
from src.sarif_exporter import export_sarif
import asyncio, json

auditor = OmniAuditor(source_code, file_path="app.py", no_ui=True)
report = asyncio.run(auditor.run())
sarif = export_sarif(report, file_path="app.py")
print(json.dumps(sarif, indent=2))
```

---

## Key Features

- **Spectral CFG Analysis** — Converts Python control flow into graph Laplacians and extracts rigorous spectral invariants (Fiedler value, Von-Neumann entropy, graph energy, effective rank).
- **Statistical Anomaly Detection** — Cold-start-regularised covariance estimation with Cholesky-based Mahalanobis scoring and Rényi entropy deviation fusion.
- **Security Scanning** — Six specialised AST scanners detecting dangerous calls, SQL injection, path traversal, hardcoded secrets, unsafe deserialization, and dynamic execution.
- **Baseline Drift Detection** — Save spectral snapshots and later compare them to detect structural degradation (`IMPROVED`, `STABLE`, `DEGRADED`, `FRACTURED`).
- **Rich Live UI** — Progress bars, per-function metrics tables, colour-coded security findings, and animated risk assessment footer.
- **JSON Export** — Compact or pretty-printed JSON for CI/CD ingestion.

---

## Project Structure

```
omni-auditor/
├── src/
│   ├── main.py                    # Async orchestrator, FusionEngine, Rich UI, CLI
│   ├── engine/
│   │   ├── analyzer.py            # AST → CFG → Spectral Graph Theory
│   │   ├── validator.py           # Statistical validation & anomaly scoring
│   │   ├── security.py            # Vulnerability scanners
│   │   ├── diff.py                # Spectral drift / baseline comparison
│   │   └── baseline.py            # Baseline persistence & snapshot builder
│   ├── reporting/
│   │   └── json_exporter.py       # JSON serialization helper
│   └── sarif_exporter.py          # SARIF v2.1.0 generator
├── github-app/
│   ├── main.py                    # FastAPI webhook handler
│   ├── analyzer.py                # PR file fetcher & subprocess runner
│   ├── commenter.py               # Markdown PR comment builder
│   ├── baseline.py                # Drift computation for PRs
│   ├── config.py                  # Pydantic settings
│   └── tests/                     # GitHub App unit tests
├── vscode-extension/
│   ├── src/
│   │   ├── extension.ts           # Extension entry point
│   │   ├── providers/
│   │   │   ├── diagnostics.ts     # Problem panel integration
│   │   │   ├── hoverProvider.ts   # Hover tooltips
│   │   │   └── decorationProvider.ts  # Gutter icons & highlights
│   │   ├── commands/
│   │   │   └── runAnalysis.ts     # Analysis command
│   │   ├── panels/
│   │   │   └── riskDashboard.ts   # Chart.js webview
│   │   └── utils/
│   │       └── apiClient.ts       # Python CLI wrapper
│   └── package.json
├── tests/                         # Core engine & orchestrator tests
├── .github/workflows/ci.yml       # Matrix CI (Windows/Ubuntu × Python 3.10/3.11/3.12)
├── action.yml                     # Composite GitHub Action
├── pyproject.toml
└── README.md
```

---

## Development

```bash
# Run all tests
python -m unittest discover tests/ -v
python -m unittest discover github-app/tests/ -v

# Compile VS Code extension
cd vscode-extension && npm run compile

# Package extension
cd vscode-extension && vsce package
```

---

## Limitations

- **Cold-start false positives on complex benign files** — Omni-Auditor's
  spectral analyzer assigns higher structural scores to large, deeply nested
  files (including `analyzer.py` itself). In cold-start mode (no saved baseline),
  this can produce HIGH or CRITICAL tiers for benign code with complex control
  flow. The tool is designed for **drift detection** (`--save-baseline` →
  `--diff`): once a known-good baseline is saved, structural complexity is
  normalised against it and false positives collapse. For standalone file
  scanning without a baseline, rely on the **security findings list**, not
  the risk tier alone.

## License

[MIT](LICENSE) © 2026 Omni-Auditor Contributors
