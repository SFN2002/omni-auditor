# Omni-Auditor

> Research-grade static analysis engine combining Spectral Graph Theory, Rényi Entropy, and Mahalanobis Distance to detect deep structural fragility in code.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Code style: strict typing](https://img.shields.io/badge/code%20style-strict%20typing-blue)
![PyPI](https://img.shields.io/pypi/v/omni-auditor)
![CI](https://github.com/SFN2002/omni-auditor/actions/workflows/ci.yml/badge.svg)

---

## Installation

```bash
pip install omni-auditor
```

Requirements: Python ≥3.10, `numpy`, `scipy`, `rich`.

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

---

## Architecture

Omni-Auditor is built around four engine subsystems orchestrated by an async pipeline in `src/main.py`:

| Engine | File | Purpose |
|--------|------|---------|
| **Analyzer** | `src/engine/analyzer.py` | Parses AST → Control Flow Graphs → Spectral Graph Theory (Laplacians, eigen-decomposition, entropy, modularity). Produces a 56-D structural feature vector. |
| **Validator** | `src/engine/validator.py` | Multivariate statistical anomaly detection: Mahalanobis distance, Rényi-2 entropy (discrete & differential), z-score fusion. Produces a 16-D anomaly vector. |
| **Security** | `src/engine/security.py` | AST-based vulnerability scanning: SQL injection, path traversal, hardcoded secrets, dangerous calls, unsafe deserialization, dynamic execution. Produces an 18-D threat vector. |
| **Diff** | `src/engine/diff.py` | Spectral drift detection between baselines: Laplacian Frobenius distance, eigenvalue KL drift, Fiedler vector shift, modularity delta, security delta. |

The **FusionEngine** adaptively weights the three vectors (56-D + 16-D + 18-D = 90-D) based on security severity, then computes a unified risk score in `[0, 1]` with tier `LOW | MEDIUM | HIGH | CRITICAL`.

---

## CLI Flags

| Flag | Description |
|------|-------------|
| `file` | Path to the Python source file to analyse (required). |
| `--json` | Skip the Rich UI and emit a compact JSON report to stdout. |
| `--verbose` | Print detailed per-function spectral metrics before the final report. |
| `--threshold FLOAT` | Override the CRITICAL risk tier threshold (default: `0.7`). |
| `--save-baseline ID` | Persist the current analysis snapshot as a baseline under the given project ID. |
| `--diff ID` | Load a saved baseline and compute structural drift against the current file. |

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
│   └── reporting/
│       └── json_exporter.py       # JSON serialization helper
├── tests/
│   └── test_diff.py               # pytest suite for diff engine & pipeline
├── requirements.txt               # numpy, scipy, rich
├── test_sample.py                 # Demo file with intentional security sinks
└── .omni_cache/                   # Pickle cache & JSON baselines
```

---

## Roadmap

- [ ] **Multi-language support** — Extend AST parsing to JavaScript / TypeScript via tree-sitter.
- [ ] **VS Code Extension** — Inline risk annotations and spectral metrics in the editor.
- [ ] **GitHub App** — Automated PR comments with drift reports and security findings.
- [ ] **SaaS Dashboard** — Historical trend visualisation for team-wide code health.

---

## License

[MIT](LICENSE) © 2026 Omni-Auditor Contributors
