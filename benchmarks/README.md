# Omni-Auditor Benchmark Framework

This directory contains a reproducible benchmark framework for evaluating
Omni-Auditor against Bandit on a labelled dataset of real Python files.

> **Note:** The actual dataset files are **not** committed to git. Only the
> framework scripts are tracked. Run the collection script to download the
> dataset locally.

## Structure

| File | Purpose |
|------|---------|
| `collect_dataset.py` | Download and label Python files from public GitHub repositories. |
| `benchmark.py` | Run Omni-Auditor and Bandit, compute metrics, and generate a report. |
| `data/` | Generated dataset, cache, and benchmark outputs (gitignored). |

## Quick Start

1. **Set a GitHub token** (optional but strongly recommended to avoid rate limits):

   ```bash
   export GITHUB_TOKEN=ghp_xxx
   ```

2. **Collect the dataset**:

   ```bash
   python benchmarks/collect_dataset.py --output benchmarks/data/dataset.json
   ```

   This downloads at least 500 Python files (balanced between vulnerable and
   benign sources) and caches them in `benchmarks/data/cache/`.

3. **Run the benchmark**:

   ```bash
   python benchmarks/benchmark.py --dataset benchmarks/data/dataset.json
   ```

   Outputs:
   - `benchmarks/data/benchmark_results.json` — structured results.
   - `benchmarks/data/BENCHMARKS.md` — human-readable report.

   To skip the Bandit comparison:

   ```bash
   python benchmarks/benchmark.py --skip-bandit
   ```

## Methodology

### Dataset

* **Vulnerable** files are sourced from repositories that collect CVE snippets
  and vulnerable code patterns.
* **Benign** files are sourced from highly-starred, actively maintained Python
  projects.
* Files are labelled at the file level as `VULNERABLE` or `BENIGN`.

### Evaluation

* A file is considered **flagged** by Omni-Auditor if its risk tier is
  `HIGH`/`CRITICAL` or if any security finding is reported.
* A file is considered **flagged** by Bandit if Bandit reports any issue.
* Metrics reported: precision, recall, F1, accuracy.
* Per-tier confusion matrix for Omni-Auditor.
* Per-category precision/recall for Omni-Auditor security categories.
* Stratified 5-fold cross-validation F1 scores.
* 95% confidence intervals via bootstrap (1,000 iterations).

## Extending

To add new sources, edit the `VULNERABLE_SOURCES` or `BENIGN_SOURCES` lists in
`collect_dataset.py` and rerun the collection step.
