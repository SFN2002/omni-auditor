"""Unit tests for src.engine.security scanners."""

from __future__ import annotations

import unittest

from src.engine.security import SafetyGuard


class TestSecurity(unittest.TestCase):
    """One test per security scanner."""

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
