# extracted from main.py
"""Omni-Auditor — top-level entry point (thin re-export shim)."""

from __future__ import annotations

from .fusion import FinalReport, FusionEngine
from .ui import RichUIRenderer
from .cli import AnalysisPipeline, OmniAuditor, main

__all__ = [
    "AnalysisPipeline",
    "FusionEngine",
    "FinalReport",
    "OmniAuditor",
    "RichUIRenderer",
    "main",
]

if __name__ == "__main__":
    main()
