# Contributing to Omni-Auditor

Thank you for your interest in improving Omni-Auditor! This document outlines the conventions and workflows we follow.

---

## Code Style Requirements

- **Strict typing**: Every module must begin with `from __future__ import annotations`. All public functions, methods, and dataclasses must have type hints.
- **NumPy / SciPy for math**: Use vectorised NumPy operations and SciPy linear-algebra routines. Avoid basic Python loops for numerical computation.
- **Immutable data**: Prefer `frozen=True` dataclasses for analysis results. Pass NumPy arrays with explicit `NDArray[np.float64]` types.
- **Docstrings**: Use Google-style docstrings for modules, classes, and public methods.
- **No runtime dependencies beyond the core stack**: `numpy`, `scipy`, `rich`, and the Python standard library.

---

## How to Run Tests

```bash
# Using the standard library unittest runner
python -m unittest discover tests/

# Using pytest (install it first)
pip install pytest
pytest tests/
```

All new features must include tests. The existing test suite lives in `tests/test_diff.py` and validates the diff engine, baseline manager, and full analysis pipeline.

---

## How to Add a New Security Scanner

1. **Create a scanner class** in `src/engine/security.py` (or import it from a new submodule).
2. Inherit from `ast.NodeVisitor` or implement a `scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]` interface.
3. Emit `ThreatSignature` objects with severity (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`), category string, line number, node path, and confidence score in `[0, 1]`.
4. Register the scanner in `VulnerabilityScanner.__init__`.
5. Add a test case in `tests/test_diff.py` or a new `tests/test_security.py`.
6. Update the **Architecture** table in `README.md` if the scanner introduces a new category.

Example skeleton:

```python
class MyNewScanner:
    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        visitor = self._Visitor()
        visitor.visit(tree)
        return visitor.threats

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.threats: list[ThreatSignature] = []

        def visit_Call(self, node: ast.Call) -> None:
            # ... detection logic ...
            self.generic_visit(node)
```

---

## How to Add a New Language Parser

Omni-Auditor is currently Python-centric, but the engine architecture is language-agnostic beyond the AST parsing stage.

1. **Implement a parser** that produces `ControlFlowGraph` objects from your target language.
2. The CFG must expose:
   - `blocks: dict[str, BasicBlock]`
   - `entry_block`, `exit_block`
   - `edge_list: list[tuple[str, str]]`
   - `get_index_mapping()`
   - `to_adjacency_matrix(symmetrize=True)`
3. Feed the CFG into `SpectralGraphAnalyzer` — the spectral pipeline is fully decoupled from the AST.
4. Wire the new parser into `src/main.py` behind a language-detection heuristic or CLI flag.
5. Add tests that verify the parser produces deterministic CFGs for simple functions.

---

## Pull Request Checklist

- [ ] `python -m src.main test_sample.py` renders the Rich UI without errors.
- [ ] `python -m src.main test_sample.py --json` produces valid JSON.
- [ ] `python -m src.main test_sample.py --save-baseline pr_test && python -m src.main test_sample.py --diff pr_test` works end-to-end.
- [ ] Tests pass (`python -m unittest discover tests/`).
- [ ] No new warnings from type checkers (mypy / pyright) on modified files.
- [ ] README.md updated if user-facing behaviour changed.

---

## Questions?

Open a GitHub Discussion or ping an existing issue. We are happy to help!
