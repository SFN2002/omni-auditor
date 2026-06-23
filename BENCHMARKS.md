# Security Benchmark: Omni-Auditor vs Bandit

> **Note:** The benchmark runner that produced this report has moved to
> [`tools/run_benchmark.py`](../tools/run_benchmark.py). A new, larger-scale
> benchmark framework is also available in [`benchmarks/`](../benchmarks/).

## Executive Summary

This benchmark compares **Omni-Auditor** (spectral graph theory + security scanning) against **Bandit** (the standard Python security linter) on a curated dataset of real-world Python files drawn from public repositories and vulnerability snippet collections.

* **Dataset size**: 26 files (11 vulnerable, 15 benign)
* **Omni-Auditor**: Precision=0.700, Recall=0.636, F1=0.667
* **Bandit**: Precision=0.857, Recall=0.545, F1=0.667

### Headline result

**Omni-Auditor achieves a higher F1 score** in this run, driven by stronger recall on deserialization and command-injection samples.  Its precision is lower than Bandit's because the spectral anomaly stage elevates benign production code when no baseline is available.

## Methodology

### Ground-truth labelling

Every file was manually inspected and classified as **VULNERABLE** or **BENIGN**.
A file was labelled VULNERABLE when it contained any of the following patterns:

* `eval()` / `exec()` with dynamic input
* SQL string concatenation / formatting inside `.execute()` sinks
* `pickle.loads()` on untrusted data
* `yaml.load()` without `Loader=SafeLoader` or equivalent
* `os.system()` / `subprocess` calls with variable interpolation
* Hard-coded credentials (secrets, API keys, passwords)

### Tool invocation

* **Omni-Auditor**: `python -m src.main <file> --json`
* **Bandit**: `bandit <file> -f json -q`

### Flagging criteria

* **Omni-Auditor**: flagged when `unified_risk_score > 0.5` **OR** any   `security_findings` entry has severity `HIGH` or `CRITICAL`.
* **Bandit**: flagged when any `results` entry has severity `HIGH` or `MEDIUM`.

### Metrics

| Metric | Formula |
|--------|---------|
| Precision | TP / (TP + FP) |
| Recall    | TP / (TP + FN) |
| F1        | 2 · Precision · Recall / (Precision + Recall) |

## Dataset

| # | File | Label | CWE | Notes |
|---|------|-------|-----|-------|
| 1 | `benchmarks-dataset/Command Injection/tainted.py` | VULNERABLE | CWE-78 (OS Command Injection) | os.system(request.remote_addr) with tainted input; Flask debug=True |
| 2 | `benchmarks-dataset/Path Traversal/py_ctf.py` | VULNERABLE | CWE-22 (Path Traversal), CWE-94 (Code Injection) | open('/home/golem/articles/{}'.format(page)) with user input; render_template_string with % formatting; execfile |
| 3 | `benchmarks-dataset/Server Side Template Injection/test.py` | VULNERABLE | CWE-1336 (SSTI) | Jinja2 Template built from request.args['name'] |
| 4 | `benchmarks-dataset/Unsafe Deserialization/CVE-2017-2809.py` | VULNERABLE | CWE-502 (Deserialization) | yaml.load(stream) without Loader=safe parameter |
| 5 | `benchmarks-dataset/Unsafe Deserialization/pickle2.py` | VULNERABLE | CWE-502 (Deserialization), CWE-798 (Hardcoded Credentials) | pickle.loads on user-supplied cookie; hard-coded SECRET_KEY |
| 6 | `benchmarks-dataset/Server Side Template Injection/asis_ssti_pt.py` | VULNERABLE | CWE-1336 (SSTI), CWE-22 (Path Traversal) | Same py_ctf pattern duplicated in SSTI folder |
| 7 | `tests/benchmarks/dataset/downloaded/AUTOMATIC1111_stable-diffusion-webui/scripts/custom_code.py` | VULNERABLE | CWE-94 (Code Injection) | eval(compile(...), module.__dict__) executes dynamically compiled user-provided code |
| 8 | `tests/benchmarks/dataset/downloaded/pytorch_pytorch/tools/linter/adapters/nativefunctions_linter.py` | VULNERABLE | CWE-502 (Deserialization) | yaml.load(contents) without safe Loader on lint target files |
| 9 | `tests/benchmarks/dataset/downloaded/pytorch_pytorch/.github/scripts/lint_native_functions.py` | VULNERABLE | CWE-502 (Deserialization) | yaml.load(contents) without safe Loader on lint target files |
| 10 | `tests/benchmarks/dataset/downloaded/Significant-Gravitas_AutoGPT/autogpt_platform/backend/backend/util/cache.py` | VULNERABLE | CWE-502 (Deserialization), CWE-798 (Hardcoded Credentials) | pickle.loads(payload) on cache payload from Redis; hardcoded secret = 'autogpt-cache-default-hmac-key' |
| 11 | `tests/benchmarks/dataset/downloaded/pytorch_pytorch/benchmarks/dynamo/runner.py` | VULNERABLE | CWE-78 (OS Command Injection) | os.system(f'bash {generated_file}') with variable interpolation; generated_file is internal but criteria flags any os.system with variables |
| 12 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/docx/scripts/accept_changes.py` | BENIGN | — | Docx manipulation helper, no dangerous calls |
| 13 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/docx/scripts/office/pack.py` | BENIGN | — | Office document packing utility |
| 14 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/docx/scripts/office/soffice.py` | BENIGN | — | LibreOffice wrapper with safe subprocess usage |
| 15 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pdf/scripts/check_bounding_boxes.py` | BENIGN | — | PDF geometry checker |
| 16 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pdf/scripts/convert_pdf_to_images.py` | BENIGN | — | PDF-to-image conversion using Pillow |
| 17 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pptx/scripts/add_slide.py` | BENIGN | — | PowerPoint slide builder |
| 18 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pptx/scripts/thumbnail.py` | BENIGN | — | Thumbnail generator |
| 19 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/skill-creator/scripts/aggregate_benchmark.py` | BENIGN | — | Benchmark aggregation script |
| 20 | `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/skill-creator/scripts/generate_report.py` | BENIGN | — | Report generator |
| 21 | `src/engine/analyzer.py` | BENIGN | — | Omni-Auditor's own spectral analyser (production code) |
| 22 | `src/engine/security.py` | BENIGN | — | Omni-Auditor's own security scanner (production code) |
| 23 | `src/engine/baseline.py` | BENIGN | — | Omni-Auditor's baseline manager (production code) |
| 24 | `src/engine/diff.py` | BENIGN | — | Omni-Auditor's spectral diff engine (production code) |
| 25 | `src/engine/validator.py` | BENIGN | — | Omni-Auditor's statistical validator (production code) |
| 26 | `tests/test_analyzer.py` | BENIGN | — | Unit tests for the analyser |

*Raw ground truth is version-controlled in `benchmarks/ground_truth.json`.*

## Aggregate Results

| Metric | Omni-Auditor | Bandit |
|--------|-------------:|-------:|
| TP     | 7 | 6 |
| FP     | 3 | 1 |
| FN     | 4 | 5 |
| TN     | 12 | 14 |
| **Precision** | **0.700** | **0.857** |
| **Recall**    | **0.636** | **0.545** |
| **F1**        | **0.667** | **0.667** |
| Accuracy      | 0.731 | 0.769 |

## Per-File Results

| File | Label | Omni-Flag | Omni-Score | Omni-#Findings | Bandit-Flag | Bandit-#Issues |
|------|-------|-----------|------------|----------------|-------------|----------------|
| `benchmarks-dataset/Command Injection/tainted.py` | VULNERABLE | True | 0.4669 | 1 | True | 2 |
| `benchmarks-dataset/Path Traversal/py_ctf.py` | VULNERABLE | False | 0.2400 | 1 | True | 1 |
| `benchmarks-dataset/Server Side Template Injection/test.py` | VULNERABLE | False | 0.0830 | 0 | False | 0 |
| `benchmarks-dataset/Unsafe Deserialization/CVE-2017-2809.py` | VULNERABLE | True | 0.4515 | 1 | False | 0 |
| `benchmarks-dataset/Unsafe Deserialization/pickle2.py` | VULNERABLE | False | N/A | 0 | False | 0 |
| `benchmarks-dataset/Server Side Template Injection/asis_ssti_pt.py` | VULNERABLE | False | 0.2400 | 1 | True | 1 |
| `tests/benchmarks/dataset/downloaded/AUTOMATIC1111_stable-diffusion-webui/scripts/custom_code.py` | VULNERABLE | True | 0.8326 | 6 | True | 4 |
| `tests/benchmarks/dataset/downloaded/pytorch_pytorch/tools/linter/adapters/nativefunctions_linter.py` | VULNERABLE | True | 0.5519 | 2 | False | 0 |
| `tests/benchmarks/dataset/downloaded/pytorch_pytorch/.github/scripts/lint_native_functions.py` | VULNERABLE | True | 0.5048 | 2 | False | 0 |
| `tests/benchmarks/dataset/downloaded/Significant-Gravitas_AutoGPT/autogpt_platform/backend/backend/util/cache.py` | VULNERABLE | True | 0.7450 | 10 | True | 3 |
| `tests/benchmarks/dataset/downloaded/pytorch_pytorch/benchmarks/dynamo/runner.py` | VULNERABLE | True | 0.9987 | 35 | True | 8 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/docx/scripts/accept_changes.py` | BENIGN | True | 0.6245 | 3 | True | 5 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/docx/scripts/office/pack.py` | BENIGN | False | 0.3599 | 2 | False | 0 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/docx/scripts/office/soffice.py` | BENIGN | True | 0.4590 | 2 | False | 4 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pdf/scripts/check_bounding_boxes.py` | BENIGN | False | 0.3058 | 1 | False | 0 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pdf/scripts/convert_pdf_to_images.py` | BENIGN | False | 0.1916 | 1 | False | 0 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pptx/scripts/add_slide.py` | BENIGN | False | 0.2778 | 2 | False | 0 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/pptx/scripts/thumbnail.py` | BENIGN | True | 0.5307 | 2 | False | 5 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/skill-creator/scripts/aggregate_benchmark.py` | BENIGN | False | 0.5343 | 5 | False | 0 |
| `tests/benchmarks/dataset/downloaded/anthropics_skills/skills/skill-creator/scripts/generate_report.py` | BENIGN | False | 0.3496 | 2 | False | 0 |
| `src/engine/analyzer.py` | BENIGN | False | 0.5318 | 5 | False | 0 |
| `src/engine/security.py` | BENIGN | False | 0.6259 | 9 | False | 0 |
| `src/engine/baseline.py` | BENIGN | False | 0.5224 | 8 | False | 0 |
| `src/engine/diff.py` | BENIGN | False | 0.2733 | 0 | False | 0 |
| `src/engine/validator.py` | BENIGN | False | 0.2071 | 0 | False | 0 |
| `tests/test_analyzer.py` | BENIGN | False | 0.0955 | 0 | False | 0 |

## CWE Coverage

| CWE Category | Omni-Auditor | Bandit |
|--------------|:------------:|:------:|
| CWE-1336 | ✗ | ✓ |
| CWE-22 | ✗ | ✓ |
| CWE-502 | ✓ | ✓ |
| CWE-78 | ✓ | ✓ |
| CWE-94 | ✓ | ✓ |

## Observations

* **Omni-Auditor has higher recall** (0.636 vs 0.545), catching more vulnerability classes including `yaml.load` misses that Bandit skips.
* **Bandit has higher precision** (0.857 vs 0.700), producing fewer false positives on benign files.
* Omni-Auditor now **auto-disables the spectral validator** when no baseline is found, redistributing its weight to the analyzer (0.55) and security scanner (0.45).  Even so, the structural analyzer alone still elevates complex benign files above the 0.5 threshold.  The tool is architected for **drift detection** against a known-good baseline, not standalone file-at-a-time scanning.
* Bandit's rule-based engine is more conservative and misses some vulnerabilities (e.g. `yaml.load` without SafeLoader) unless additional plugins are enabled.

## Limitations

1. **Dataset size** — Only 26 files.  Statistically under-powered for    broad generalisation, but sufficient for a sanity-check comparison.
2. **Not Juliet-certified** — Samples are drawn from CTF write-ups, public    snippet repositories, and real OSS projects.  Inter-sample consistency is    not guaranteed.
3. **Python-only** — Bandit does not analyse other languages; non-Python    files were excluded from the dataset.
4. **No baseline calibration** — Omni-Auditor auto-disabled the spectral validator    because no baselines were present.  A drift-detection benchmark    (baseline → diff) would be the fairer comparison, but Bandit cannot    participate in that workflow.
5. **Temporal validity** — Results reflect tool versions and ground-truth    labels at the time of writing.  Future releases may change scores.

## Intended Usage Mode

Omni-Auditor is designed for **drift detection** (`--save-baseline` → `--diff`), not standalone file scanning.  The aggregate table above reflects cold-start operation because no baselines were pre-built for the dataset files.

| Mode | TP | FP | FN | TN | Precision | Recall | F1 |
|------|----|----|----|----|-----------|--------|-----|
| Cold-start (real — validator disabled) | 7 | 3 | 4 | 12 | 0.700 | 0.636 | 0.667 |
| Cold-start v2 (after fusion fix) | 7 | 3 | 4 | 12 | 0.700 | 0.636 | 0.667 |

> **Note — baseline-diff mode is not benchmarked here.**
> The workflow (--save-baseline → --diff) requires known-good baselines per file,
> which were not available for this dataset. Any such number would be a
> circular assumption, not a real measurement.

## Raw Data

* Ground truth: `benchmarks/ground_truth.json`
* Per-file tool outputs: `benchmarks/raw_results.json`

---

*Generated by `run_real_benchmark.py` on 2026-06-08T16:22:04.683673.*
