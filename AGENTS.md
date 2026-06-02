# Omni-Auditor — Agent Guide

## Build

```bash
python -m src.main <file> --json
```

## Extension

```bash
cd vscode-extension && npm run compile
```

## Test

```bash
python -m unittest discover tests/
```

## Package Extension

```bash
cd vscode-extension && vsce package
```

## Conventions

- Strict typing (`from __future__ import annotations`).
- NumPy arrays use `NDArray[np.float64]`.
- Cache files: `.npz` + `.json` (never pickle).
- Graceful error handling; no unhandled exceptions in user-facing code.
