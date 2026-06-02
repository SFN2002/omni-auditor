"""
Omni-Auditor — Security Analysis Engine (security.py)
============================================================

This module performs rigorous, AST-based static vulnerability detection
on Python source code.  It is fully decoupled from ``analyzer.py`` and
operates directly on the ``ast`` module's parse tree.

Architecture
------------

1.  **ThreatSignature** — Immutable record of a single security finding.
2.  **Base Scanners** — A family of specialised ``ast.NodeVisitor``
    subclasses, each targeting a specific attack class:
    *   ``DangerousCallScanner`` — eval, exec, subprocess, os.system, ctypes, etc.
    *   ``SQLInjectionScanner`` — dynamic string construction inside
        ``cursor.execute()`` / ``executemany()``.
    *   ``PathTraversalScanner`` — unsanitised paths fed to ``open()``,
        ``shutil``, ``pathlib``, etc.
    *   ``SecretScanner`` — hard-coded secrets detected via entropy-augmented
        regex on assignment targets.
    *   ``DeserializationScanner`` — pickle, marshal, yaml unsafe loads.
    *   ``DynamicExecutionScanner`` — compile, __import__, getattr/setattr
        with dynamic names, ``types.CodeType``, etc.
3.  **VulnerabilityScanner** — Orchestrates all sub-scanners over a single
    AST and returns an aggregated list of ``ThreatSignature`` objects.
4.  **SecurityReportBuilder** — Computes normalised category counts,
    severity entropy, and a fixed-dimension NumPy feature vector for
    downstream fusion in ``main.py``.
5.  **SafetyGuard** — High-level façade: ``source_code → SecurityReport``.

All public interfaces are strictly typed (``from __future__ import annotations``).
"""

from __future__ import annotations

import ast
import collections
import re
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ENTROPY_SECRET: float = 6.0  # bits, used to cap confidence scaling

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThreatSignature:
    """Immutable record of a single security finding."""

    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    category: str
    line_number: int
    node_path: str
    confidence_score: float


@dataclass(frozen=True)
class SecurityReport:
    """Aggregated security posture exported to ``main.py``."""

    threats: list[ThreatSignature]
    feature_vector: NDArray[np.float64]
    category_counts: dict[str, int]
    severity_counts: dict[str, int]
    severity_entropy: float
    category_entropy: float
    total_threats: int


# ---------------------------------------------------------------------------
# AST utilities
# ---------------------------------------------------------------------------


def _resolve_attribute(node: ast.expr) -> str:
    """Resolve an ``ast.Attribute`` chain into a dotted string.

    Examples
    --------
    * ``pickle.loads``  → ``"pickle.loads"``
    * ``cursor.execute`` → ``"cursor.execute"``
    * ``Name(id='eval')`` → ``"eval"``
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _is_string_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _get_string_value(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_target_name(node: ast.expr) -> str | None:
    """Extract a human-readable name from an assignment target."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _resolve_attribute(node)
    if isinstance(node, ast.Subscript):
        # e.g. d['password']
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return node.slice.value
    return None


def _shannon_entropy(s: str) -> float:
    """Compute Shannon entropy in bits of a string."""
    if not s:
        return 0.0
    counts = collections.Counter(s)
    probs = np.array(list(counts.values()), dtype=np.float64) / len(s)
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))


class _UserInputFinder(ast.NodeVisitor):
    """Heuristic visitor that detects whether an AST subtree references
    common user-controlled input names (request, args, input, etc.)."""

    _USER_INPUT_NAMES: frozenset[str] = frozenset({
        "request", "input", "args", "argv", "params", "user_input",
        "form", "query", "data", "path", "filename", "file", "upload",
        "stream", "body", "content", "headers", "cookies",
    })

    def __init__(self) -> None:
        self.found: bool = False

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id.lower() in self._USER_INPUT_NAMES:
            self.found = True

    def generic_visit(self, node: ast.AST) -> None:
        if not self.found:
            super().generic_visit(node)


def _contains_user_input(node: ast.AST) -> bool:
    finder = _UserInputFinder()
    finder.visit(node)
    return finder.found


# ---------------------------------------------------------------------------
# Sub-scanners
# ---------------------------------------------------------------------------


class DangerousCallScanner:
    """Detects invocation of dangerous built-ins and standard-library APIs."""

    _DANGEROUS_NAMES: frozenset[str] = frozenset({
        "eval", "exec", "compile", "__import__",
    })

    _DANGEROUS_ATTRS: frozenset[str] = frozenset({
        "os.system", "os.popen", "os.spawnl", "os.spawnle", "os.spawnlp",
        "os.spawnlpe", "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe",
        "platform.popen",
        "ctypes.CDLL", "ctypes.cdll", "ctypes.windll", "ctypes.oledll",
        "subprocess.Popen", "subprocess.call", "subprocess.run",
        "subprocess.check_output", "subprocess.check_call",
    })

    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        visitor = self._Visitor()
        visitor.visit(tree)
        return visitor.threats

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.threats: list[ThreatSignature] = []

        def _has_shell_true(self, node: ast.Call) -> bool:
            for kw in node.keywords:
                if kw.arg == "shell":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        return True
            return False

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            name = _resolve_attribute(node.func)
            line = getattr(node, "lineno", 0)

            if name in DangerousCallScanner._DANGEROUS_NAMES:
                self.threats.append(
                    ThreatSignature(
                        severity="CRITICAL",
                        category="dangerous_call",
                        line_number=line,
                        node_path=name,
                        confidence_score=0.95,
                    )
                )
            elif name in DangerousCallScanner._DANGEROUS_ATTRS:
                if name.startswith("subprocess"):
                    has_shell = self._has_shell_true(node)
                    severity = "CRITICAL" if has_shell else "HIGH"
                    confidence = 0.95 if has_shell else 0.88
                elif name.startswith(("os.system", "os.popen", "platform.popen")):
                    severity = "CRITICAL"
                    confidence = 0.95
                else:
                    severity = "HIGH"
                    confidence = 0.85
                self.threats.append(
                    ThreatSignature(
                        severity=severity,
                        category="dangerous_call",
                        line_number=line,
                        node_path=name,
                        confidence_score=confidence,
                    )
                )
            self.generic_visit(node)


class SQLInjectionScanner:
    """Detects SQL injection sinks via heuristic analysis of dynamic SQL
    string construction inside ``.execute()`` / ``.executemany()`` calls."""

    _SQL_METHODS: frozenset[str] = frozenset({
        "execute", "executemany", "executescript",
    })

    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        visitor = self._Visitor()
        visitor.visit(tree)
        return visitor.threats

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.threats: list[ThreatSignature] = []

        @staticmethod
        def _is_dynamic_sql(node: ast.expr) -> bool:
            if isinstance(node, ast.JoinedStr):
                return True
            if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Mod)
            ):
                return True
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
                    return True
                if isinstance(node.func, ast.Name) and node.func.id == "format":
                    return True
            return False

        @staticmethod
        def _confidence(node: ast.expr) -> float:
            if isinstance(node, ast.JoinedStr):
                return 0.92
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
                return 0.88
            if isinstance(node, ast.Call):
                return 0.85
            if isinstance(node, ast.BinOp):
                return 0.80
            return 0.65

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if isinstance(node.func, ast.Attribute) and node.func.attr in SQLInjectionScanner._SQL_METHODS:
                if node.args and self._is_dynamic_sql(node.args[0]):
                    path = _resolve_attribute(node.func)
                    self.threats.append(
                        ThreatSignature(
                            severity="HIGH",
                            category="sql_injection",
                            line_number=getattr(node, "lineno", 0),
                            node_path=path,
                            confidence_score=self._confidence(node.args[0]),
                        )
                    )
            self.generic_visit(node)


class PathTraversalScanner:
    """Detects file-system operations that accept potentially unsanitised
    user input as path arguments."""

    _FILE_METHODS: frozenset[str] = frozenset({
        "open",
        "os.path.join",
        "shutil.copy", "shutil.copy2", "shutil.copyfile",
        "shutil.copytree", "shutil.move",
    })

    _PATHLIB_IO_ATTRS: frozenset[str] = frozenset({
        "read_text", "write_text", "read_bytes", "write_bytes", "open",
    })

    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        visitor = self._Visitor()
        visitor.visit(tree)
        return visitor.threats

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.threats: list[ThreatSignature] = []

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            name = _resolve_attribute(node.func)
            if name == "open" or name in PathTraversalScanner._FILE_METHODS:
                if node.args:
                    arg = node.args[0]
                    is_literal = _is_string_literal(arg)
                    has_user_input = _contains_user_input(arg)

                    if not is_literal or has_user_input:
                        confidence = 0.78 if has_user_input else 0.62
                        self.threats.append(
                            ThreatSignature(
                                severity="MEDIUM",
                                category="path_traversal",
                                line_number=getattr(node, "lineno", 0),
                                node_path=name,
                                confidence_score=confidence,
                            )
                        )

            # Detect pathlib.Path(...).read_text() / .write_text() / .open() chains
            if isinstance(node.func, ast.Attribute) and node.func.attr in PathTraversalScanner._PATHLIB_IO_ATTRS:
                inner = node.func.value
                if isinstance(inner, ast.Call):
                    inner_name = _resolve_attribute(inner.func)
                    if inner_name in ("pathlib.Path", "Path") and inner.args:
                        arg = inner.args[0]
                        is_literal = _is_string_literal(arg)
                        has_user_input = _contains_user_input(arg)
                        if not is_literal or has_user_input:
                            confidence = 0.78 if has_user_input else 0.62
                            self.threats.append(
                                ThreatSignature(
                                    severity="MEDIUM",
                                    category="path_traversal",
                                    line_number=getattr(node, "lineno", 0),
                                    node_path=f"{inner_name}.{node.func.attr}",
                                    confidence_score=confidence,
                                )
                            )

            self.generic_visit(node)


class SecretScanner:
    """Detects hard-coded secrets via regex-augmented assignment analysis.

    Confidence is boosted by the Shannon entropy of the candidate literal;
    high-entropy strings are more likely to be real credentials, while
    low-entropy strings are probably placeholders.
    """

    _SECRET_PATTERN: re.Pattern[str] = re.compile(
        r"(?i)(password|passwd|pwd|secret|token|api_key|apikey|auth_key|"
        r"private_key|access_key|secret_key|db_pass|db_password|"
        r"admin_pass|smtp_pass|email_pass|bearer|api_token)"
    )

    _PLACEHOLDERS: frozenset[str] = frozenset({
        "", "password", "secret", "token", "key", "123456", "admin",
        "default", "test", "example", "placeholder", "changeme",
        "null", "none", "true", "false", "pass", "pwd",
    })

    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        visitor = self._Visitor()
        visitor.visit(tree)
        return visitor.threats

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.threats: list[ThreatSignature] = []

        def _check_secret(self, name: str | None, value: ast.expr, line: int) -> None:
            if name is None or not SecretScanner._SECRET_PATTERN.search(name):
                return
            val_str = _get_string_value(value)
            if val_str is None:
                return
            if val_str.lower() in SecretScanner._PLACEHOLDERS or len(val_str) <= 1:
                return
            entropy = _shannon_entropy(val_str)
            confidence = min(0.95, 0.55 + entropy / _MAX_ENTROPY_SECRET)
            self.threats.append(
                ThreatSignature(
                    severity="LOW",
                    category="hardcoded_secret",
                    line_number=line,
                    node_path=name,
                    confidence_score=confidence,
                )
            )

        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for target in node.targets:
                self._check_secret(
                    _get_target_name(target), node.value, getattr(node, "lineno", 0)
                )
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            if node.value is not None:
                self._check_secret(
                    _get_target_name(node.target),
                    node.value,
                    getattr(node, "lineno", 0),
                )
            self.generic_visit(node)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
            self._check_secret(
                _get_target_name(node.target),
                node.value,
                getattr(node, "lineno", 0),
            )
            self.generic_visit(node)


class DeserializationScanner:
    """Flags unsafe deserialization and data-unpickling patterns."""

    _DESER_ATTRS: frozenset[str] = frozenset({
        "pickle.loads", "pickle.load",
        "cPickle.loads", "cPickle.load",
        "marshal.loads", "marshal.load",
        "yaml.load", "yaml.unsafe_load", "yaml.unsafe_load_all",
    })

    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        visitor = self._Visitor()
        visitor.visit(tree)
        return visitor.threats

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.threats: list[ThreatSignature] = []

        @staticmethod
        def _is_unsafe_yaml(node: ast.Call, name: str) -> bool:
            if not name.startswith("yaml"):
                return True
            for kw in node.keywords:
                if kw.arg == "Loader":
                    # yaml.SafeLoader or yaml.CSafeLoader is safe
                    if isinstance(kw.value, ast.Attribute) and kw.value.attr in (
                        "SafeLoader", "CSafeLoader", "BaseLoader",
                    ):
                        return False
                    if isinstance(kw.value, ast.Name) and kw.value.id in (
                        "SafeLoader", "CSafeLoader", "BaseLoader",
                    ):
                        return False
            return True

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            name = _resolve_attribute(node.func)
            if name in DeserializationScanner._DESER_ATTRS:
                if not self._is_unsafe_yaml(node, name):
                    self.generic_visit(node)
                    return
                is_literal = len(node.args) > 0 and _is_string_literal(node.args[0])
                confidence = 0.70 if is_literal else 0.94
                self.threats.append(
                    ThreatSignature(
                        severity="CRITICAL",
                        category="deserialization",
                        line_number=getattr(node, "lineno", 0),
                        node_path=name,
                        confidence_score=confidence,
                    )
                )
            self.generic_visit(node)


class DynamicExecutionScanner:
    """Detects dynamic code execution and introspection abuse."""

    _DYNAMIC_NAMES: frozenset[str] = frozenset({
        "eval", "exec", "compile", "__import__",
    })

    _DYNAMIC_ATTRS: frozenset[str] = frozenset({
        "getattr", "setattr", "delattr",
        "importlib.import_module",
        "types.FunctionType", "types.CodeType", "types.MethodType",
    })

    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        visitor = self._Visitor()
        visitor.visit(tree)
        return visitor.threats

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.threats: list[ThreatSignature] = []

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            name = _resolve_attribute(node.func)
            line = getattr(node, "lineno", 0)

            if name in DynamicExecutionScanner._DYNAMIC_NAMES:
                self.threats.append(
                    ThreatSignature(
                        severity="CRITICAL",
                        category="dynamic_execution",
                        line_number=line,
                        node_path=name,
                        confidence_score=0.95,
                    )
                )
            elif name in DynamicExecutionScanner._DYNAMIC_ATTRS:
                if name in ("getattr", "setattr", "delattr"):
                    # If attribute name is dynamic (not a string literal) → higher risk
                    dynamic_name = (
                        len(node.args) >= 2 and not _is_string_literal(node.args[1])
                    )
                    confidence = 0.86 if dynamic_name else 0.62
                    severity = "HIGH" if dynamic_name else "MEDIUM"
                else:
                    confidence = 0.78
                    severity = "HIGH"
                self.threats.append(
                    ThreatSignature(
                        severity=severity,
                        category="dynamic_execution",
                        line_number=line,
                        node_path=name,
                        confidence_score=confidence,
                    )
                )
            self.generic_visit(node)


# ---------------------------------------------------------------------------
# Orchestrator & report builder
# ---------------------------------------------------------------------------


class VulnerabilityScanner:
    """Aggregates all sub-scanners into a single unified AST pass.

    Each sub-scanner receives the *same* AST tree independently; this is
    slightly more CPU work than a monolithic visitor but preserves strict
    decoupling between detection heuristics.
    """

    def __init__(self) -> None:
        self._scanners = [
            DangerousCallScanner(),
            SQLInjectionScanner(),
            PathTraversalScanner(),
            SecretScanner(),
            DeserializationScanner(),
            DynamicExecutionScanner(),
        ]

    def scan(self, tree: ast.AST, source_lines: list[str]) -> list[ThreatSignature]:
        threats: list[ThreatSignature] = []
        for scanner in self._scanners:
            threats.extend(scanner.scan(tree, source_lines))
        # Deduplicate by (line_number, node_path), keeping the highest severity.
        severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        deduped: dict[tuple[int, str], ThreatSignature] = {}
        for t in threats:
            key = (t.line_number, t.node_path)
            existing = deduped.get(key)
            if existing is None or severity_order[t.severity] > severity_order[existing.severity]:
                deduped[key] = t
        threats = list(deduped.values())
        # Deterministic ordering by line number for stable downstream vectors.
        threats.sort(key=lambda t: (t.line_number, t.category, t.node_path))
        return threats


class SecurityReportBuilder:
    """Constructs a ``SecurityReport`` from a raw list of ``ThreatSignature``
    objects, computing normalised histograms and a fixed-dimension feature
    vector suitable for downstream fusion."""

    _CATEGORIES: list[str] = [
        "dangerous_call",
        "sql_injection",
        "path_traversal",
        "hardcoded_secret",
        "deserialization",
        "dynamic_execution",
    ]

    def __init__(self, threats: list[ThreatSignature]) -> None:
        self.threats = threats

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _entropy(counter: collections.Counter[str]) -> float:
        total = sum(counter.values())
        if total == 0:
            return 0.0
        probs = np.array([c / total for c in counter.values()], dtype=np.float64)
        probs = probs[probs > 0]
        return float(-np.sum(probs * np.log2(probs)))

    def _build_feature_vector(
        self,
        sev_counts: collections.Counter[str],
        cat_counts: collections.Counter[str],
        total: int,
    ) -> NDArray[np.float64]:
        norm = max(total, 1)

        # 1. Severity distribution (4)
        critical = sev_counts.get("CRITICAL", 0) / norm
        high = sev_counts.get("HIGH", 0) / norm
        medium = sev_counts.get("MEDIUM", 0) / norm
        low = sev_counts.get("LOW", 0) / norm

        # 2. Category distribution (6)
        cat_vec = [cat_counts.get(c, 0) / norm for c in self._CATEGORIES]

        # 3. Global statistics
        total_log = float(np.log1p(total))
        sev_entropy = self._entropy(sev_counts)
        cat_entropy = self._entropy(cat_counts)

        confidences = (
            np.array([t.confidence_score for t in self.threats], dtype=np.float64)
            if self.threats
            else np.zeros(1, dtype=np.float64)
        )
        mean_conf = float(np.mean(confidences))
        max_conf = float(np.max(confidences))
        std_conf = float(np.std(confidences))

        has_critical = 1.0 if sev_counts.get("CRITICAL", 0) > 0 else 0.0
        has_high = 1.0 if sev_counts.get("HIGH", 0) > 0 else 0.0

        return np.array(
            [
                critical,
                high,
                medium,
                low,
                *cat_vec,
                total_log,
                sev_entropy,
                cat_entropy,
                mean_conf,
                max_conf,
                std_conf,
                has_critical,
                has_high,
            ],
            dtype=np.float64,
        )

    # -- public entry ------------------------------------------------------

    def build(self) -> SecurityReport:
        sev_counts = collections.Counter(t.severity for t in self.threats)
        cat_counts = collections.Counter(t.category for t in self.threats)
        total = len(self.threats)

        vec = self._build_feature_vector(sev_counts, cat_counts, total)

        return SecurityReport(
            threats=list(self.threats),
            feature_vector=vec,
            category_counts=dict(cat_counts),
            severity_counts=dict(sev_counts),
            severity_entropy=self._entropy(sev_counts),
            category_entropy=self._entropy(cat_counts),
            total_threats=total,
        )


# ---------------------------------------------------------------------------
# High-level façade
# ---------------------------------------------------------------------------


class SafetyGuard:
    """Main entry point: raw Python source → ``SecurityReport``."""

    def __init__(self, source_code: str) -> None:
        self.source_code: str = source_code
        self._tree: ast.AST = ast.parse(source_code)
        self._lines: list[str] = source_code.splitlines()
        self._scanner = VulnerabilityScanner()

    def scan(self) -> SecurityReport:
        """Execute the full security analysis pipeline.

        Returns
        -------
        SecurityReport
            Immutable report containing all ``ThreatSignature`` objects,
            normalised histograms, entropies, and an 18-D feature vector.
        """
        threats = self._scanner.scan(self._tree, self._lines)
        return SecurityReportBuilder(threats).build()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ThreatSignature",
    "SecurityReport",
    "DangerousCallScanner",
    "SQLInjectionScanner",
    "PathTraversalScanner",
    "SecretScanner",
    "DeserializationScanner",
    "DynamicExecutionScanner",
    "VulnerabilityScanner",
    "SecurityReportBuilder",
    "SafetyGuard",
]
