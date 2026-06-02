from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any


class JSONExporter:
    """Export analysis reports to JSON format.

    This exporter is intentionally decoupled from ``main.py`` to avoid
    circular imports.  It accepts ``report`` as ``object`` and uses
    ``dataclasses.asdict`` + recursive conversion to serialise any
    dataclass instance to JSON.
    """

    @staticmethod
    def convert(obj: Any) -> Any:
        """Recursively convert numpy arrays / tensors to plain Python."""
        if hasattr(obj, "tolist"):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: JSONExporter.convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [JSONExporter.convert(v) for v in obj]
        if isinstance(obj, tuple):
            return [JSONExporter.convert(v) for v in obj]
        if isinstance(obj, set):
            return sorted([JSONExporter.convert(v) for v in obj])
        return obj

    def export(self, report: object, output_path: Path) -> None:
        """Serialize a report dataclass to a pretty-printed JSON file."""
        payload = self.convert(dataclasses.asdict(report))
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def to_compact_json(self, report: object) -> str:
        """Serialize report to a compact JSON string (for ``--json`` CLI flag)."""
        payload = self.convert(dataclasses.asdict(report))
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
