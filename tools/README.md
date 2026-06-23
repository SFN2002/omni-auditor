# Omni-Auditor Tools

This directory contains utility scripts that are not part of the core analysis engine but support development, benchmarking, and reporting workflows.

## Contents

| Script | Purpose |
|--------|---------|
| `dashboard.py` | Streamlit dashboard for visualising Omni-Auditor JSON reports. |
| `run_benchmark.py` | Benchmark Omni-Auditor against Bandit on a curated vulnerability dataset. |

---

## `dashboard.py`

Interactive Streamlit dashboard. Upload a JSON report produced by:

```bash
python -m src.main file.py --json
```

### Run

```bash
streamlit run tools/dashboard.py
```

### Pre-load a report

```bash
streamlit run tools/dashboard.py -- --report report.json
```

### Requirements

Install the optional dashboard dependencies:

```bash
pip install streamlit plotly pandas
```

---

## `run_benchmark.py`

Runs Omni-Auditor and Bandit against a small, manually labelled dataset and produces:

- `benchmark_results.json` — per-file predictions and aggregate metrics.
- `BENCHMARKS.md` — human-readable Markdown report.

### Run

```bash
python tools/run_benchmark.py
```

### Options

| Flag | Description |
|------|-------------|
| `--root DIR` | Repository root used to resolve relative dataset paths. Defaults to the parent of `tools/`. |
| `--output-json PATH` | Path for the JSON artefact. Defaults to `benchmark_results.json`. |
| `--output-md PATH` | Path for the Markdown report. Defaults to `BENCHMARKS.md`. |

### Example

```bash
python tools/run_benchmark.py --root . --output-json out/benchmark.json --output-md out/BENCHMARKS.md
```

### Requirements

- Omni-Auditor must be runnable as `python -m src.main ...`.
- Bandit must be installed (`pip install bandit`) or available at `venv/Scripts/bandit.exe`.
