"""Expanded unit tests for src.engine.security scanners.

Each of the six sub-scanners is exercised with:
  1. A basic positive test (vulnerable code → detected)
  2. A negative test (safe code → not flagged)
  3. An edge-case test specific to that scanner's heuristics.
"""

from __future__ import annotations

import ast
import unittest

from src.engine.security import (
    DangerousCallScanner,
    SQLInjectionScanner,
    PathTraversalScanner,
    SecretScanner,
    DeserializationScanner,
    DynamicExecutionScanner,
    SafetyGuard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scan(scanner_cls: type, code: str) -> list:
    """Run a single scanner against a code snippet and return its threats."""
    tree = ast.parse(code)
    lines = code.splitlines()
    return scanner_cls().scan(tree, lines)


# ---------------------------------------------------------------------------
# DangerousCallScanner
# ---------------------------------------------------------------------------


class TestDangerousCallScanner(unittest.TestCase):
    """Detects invocation of dangerous built-ins and standard-library APIs."""

    def test_eval_detected(self) -> None:
        """eval() must produce a CRITICAL dangerous_call finding."""
        threats = _scan(DangerousCallScanner, "eval(user_input)")
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].severity, "CRITICAL")
        self.assertEqual(threats[0].category, "dangerous_call")
        self.assertEqual(threats[0].node_path, "eval")

    def test_safe_call_undetected(self) -> None:
        """A benign built-in call should not be flagged."""
        threats = _scan(DangerousCallScanner, 'print("hello world")')
        self.assertEqual(len(threats), 0)

    def test_subprocess_popen_shell_true(self) -> None:
        """subprocess.Popen with shell=True escalates to CRITICAL severity."""
        code = 'subprocess.Popen("ls -la", shell=True)'
        threats = _scan(DangerousCallScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].severity, "CRITICAL")
        self.assertEqual(threats[0].node_path, "subprocess.Popen")
        self.assertGreater(threats[0].confidence_score, 0.9)


# ---------------------------------------------------------------------------
# SQLInjectionScanner
# ---------------------------------------------------------------------------


class TestSQLInjectionScanner(unittest.TestCase):
    """Detects dynamic SQL string construction inside execute() sinks."""

    def test_concatenation_detected(self) -> None:
        """String concatenation inside .execute() must be flagged HIGH."""
        code = 'cursor.execute("SELECT * FROM users WHERE id = " + user_id)'
        threats = _scan(SQLInjectionScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "sql_injection")
        self.assertEqual(threats[0].severity, "HIGH")

    def test_static_sql_undetected(self) -> None:
        """A static string literal passed to .execute() is safe."""
        code = 'cursor.execute("SELECT 1")'
        threats = _scan(SQLInjectionScanner, code)
        self.assertEqual(len(threats), 0)

    def test_fstring_inside_execute(self) -> None:
        """An f-string with a variable inside execute() must be flagged."""
        code = 'cursor.execute(f"SELECT * FROM {table_name}")'
        threats = _scan(SQLInjectionScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "sql_injection")
        self.assertEqual(threats[0].severity, "HIGH")
        self.assertGreater(threats[0].confidence_score, 0.9)


# ---------------------------------------------------------------------------
# PathTraversalScanner
# ---------------------------------------------------------------------------


class TestPathTraversalScanner(unittest.TestCase):
    """Detects file-system operations fed potentially unsanitised paths."""

    def test_open_user_input(self) -> None:
        """open(user_input) must produce a MEDIUM path_traversal finding."""
        threats = _scan(PathTraversalScanner, "open(user_input)")
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "path_traversal")
        self.assertEqual(threats[0].severity, "MEDIUM")

    def test_open_literal_safe(self) -> None:
        """open("/etc/passwd") is a literal path and should not be flagged."""
        threats = _scan(PathTraversalScanner, 'open("/etc/passwd")')
        self.assertEqual(len(threats), 0)

    def test_os_path_join_with_user_input(self) -> None:
        """os.path.join with user-controlled first argument must be flagged."""
        code = 'os.path.join(user_input, "file.txt")'
        threats = _scan(PathTraversalScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "path_traversal")
        self.assertEqual(threats[0].node_path, "os.path.join")


# ---------------------------------------------------------------------------
# SecretScanner
# ---------------------------------------------------------------------------


class TestSecretScanner(unittest.TestCase):
    """Detects hard-coded secrets via regex-augmented assignment analysis."""

    def test_hardcoded_password_detected(self) -> None:
        """A non-placeholder password assignment must produce a LOW finding."""
        code = 'password = "super_secret_123!"'
        threats = _scan(SecretScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "hardcoded_secret")
        self.assertEqual(threats[0].severity, "LOW")

    def test_placeholder_undetected(self) -> None:
        """A known placeholder value should not be flagged as a secret."""
        code = 'password = "password"'
        threats = _scan(SecretScanner, code)
        self.assertEqual(len(threats), 0)

    def test_entropy_confidence_scaling(self) -> None:
        """High-entropy strings should have higher confidence than low-entropy."""
        high_entropy = 'api_key = "xK9#mP2$vL5@nQ8!"'
        low_entropy = 'api_key = "aaaaaaaa"'
        high_threats = _scan(SecretScanner, high_entropy)
        low_threats = _scan(SecretScanner, low_entropy)
        self.assertEqual(len(high_threats), 1)
        self.assertEqual(len(low_threats), 1)
        self.assertGreater(
            high_threats[0].confidence_score,
            low_threats[0].confidence_score,
            "High-entropy secret should have higher confidence",
        )


# ---------------------------------------------------------------------------
# DeserializationScanner
# ---------------------------------------------------------------------------


class TestDeserializationScanner(unittest.TestCase):
    """Flags unsafe deserialization and data-unpickling patterns."""

    def test_pickle_loads_detected(self) -> None:
        """pickle.loads(data) must produce a CRITICAL deserialization finding."""
        threats = _scan(DeserializationScanner, "pickle.loads(data)")
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "deserialization")
        self.assertEqual(threats[0].severity, "CRITICAL")

    def test_yaml_safe_loader_undetected(self) -> None:
        """yaml.load with SafeLoader is safe and must not be flagged."""
        code = "yaml.load(stream, Loader=yaml.SafeLoader)"
        threats = _scan(DeserializationScanner, code)
        self.assertEqual(len(threats), 0)

    def test_yaml_load_inside_class_method(self) -> None:
        """yaml.load without a safe Loader inside a class method is CRITICAL."""
        code = """
class ConfigLoader:
    def load(self, stream):
        return yaml.load(stream)
"""
        threats = _scan(DeserializationScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "deserialization")
        self.assertEqual(threats[0].node_path, "yaml.load")
        self.assertEqual(threats[0].severity, "CRITICAL")


# ---------------------------------------------------------------------------
# DynamicExecutionScanner
# ---------------------------------------------------------------------------


class TestDynamicExecutionScanner(unittest.TestCase):
    """Detects dynamic code execution and introspection abuse."""

    def test_compile_detected(self) -> None:
        """compile() must produce a CRITICAL dynamic_execution finding."""
        code = 'compile(source, "<string>", "exec")'
        threats = _scan(DynamicExecutionScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "dynamic_execution")
        self.assertEqual(threats[0].severity, "CRITICAL")

    def test_builtin_undetected(self) -> None:
        """A benign built-in call should not be flagged."""
        threats = _scan(DynamicExecutionScanner, "len([1, 2, 3])")
        self.assertEqual(len(threats), 0)

    def test_getattr_dynamic_attribute_name(self) -> None:
        """getattr with a non-literal attribute name escalates to HIGH."""
        code = "getattr(obj, dynamic_attr)"
        threats = _scan(DynamicExecutionScanner, code)
        self.assertEqual(len(threats), 1)
        self.assertEqual(threats[0].category, "dynamic_execution")
        self.assertEqual(threats[0].severity, "HIGH")
        self.assertGreater(threats[0].confidence_score, 0.8)


# ---------------------------------------------------------------------------
# Integration tests via SafetyGuard (original coverage preserved)
# ---------------------------------------------------------------------------


class TestSafetyGuardIntegration(unittest.TestCase):
    """End-to-end tests that exercise the full scanner orchestration."""

    def _severity_in(self, code: str, expected: str) -> None:
        """Helper: run SafetyGuard and assert expected severity is present."""
        report = SafetyGuard(code).scan()
        severities = [t.severity for t in report.threats]
        self.assertIn(expected, severities, f"Expected {expected} in {severities}")

    def test_dangerous_call_eval(self) -> None:
        """eval() must produce a CRITICAL finding."""
        self._severity_in("eval(user_input)\n", "CRITICAL")

    def test_sql_injection(self) -> None:
        """Dynamic SQL in .execute() must produce a HIGH finding."""
        code = 'cursor.execute("SELECT * FROM users WHERE id = " + user_input)\n'
        self._severity_in(code, "HIGH")

    def test_path_traversal(self) -> None:
        """open(user_input) must produce a MEDIUM finding."""
        self._severity_in("open(user_input)\n", "MEDIUM")

    def test_hardcoded_secret(self) -> None:
        """password = \"abc123\" must produce a LOW finding."""
        self._severity_in('password = "abc123"\n', "LOW")

    def test_deserialization_pickle(self) -> None:
        """pickle.loads(data) must produce a CRITICAL finding."""
        self._severity_in("pickle.loads(data)\n", "CRITICAL")

    def test_dynamic_execution(self) -> None:
        """__import__(name) must produce a CRITICAL finding."""
        self._severity_in("__import__(name)\n", "CRITICAL")


if __name__ == "__main__":
    unittest.main()
