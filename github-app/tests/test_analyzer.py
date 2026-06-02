"""Tests for github-app/analyzer.py."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

github_app_analyzer = __import__("github-app.analyzer", fromlist=["analyze_file"])


class TestAnalyzeFile(unittest.IsolatedAsyncioTestCase):
    """Async analysis subprocess orchestration."""

    async def test_parses_stdout_json(self) -> None:
        mock_resp = MagicMock()
        mock_resp.text = "def foo(): pass"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b'{"unified_risk_score":0.5,"risk_tier":"LOW","security_findings":[],"per_function_metrics":[]}',
                b"",
            )
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                result = await github_app_analyzer.analyze_file("owner/repo", 1, "app.py", "sha", "token")
                self.assertIsNotNone(result)
                self.assertEqual(result["risk_score"], 0.5)
                self.assertEqual(result["risk_tier"], "LOW")
                self.assertEqual(result["file_path"], "app.py")

    async def test_fallback_output_json(self) -> None:
        mock_resp = MagicMock()
        mock_resp.text = "def foo(): pass"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"not valid json", b""))

        fake_json = '{"unified_risk_score":0.75,"risk_tier":"HIGH","security_findings":[],"per_function_metrics":[]}'

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                with patch.object(Path, "exists", return_value=True):
                    with patch.object(Path, "read_text", return_value=fake_json):
                        result = await github_app_analyzer.analyze_file("owner/repo", 1, "app.py", "sha", "token")
                        self.assertIsNotNone(result)
                        self.assertEqual(result["risk_score"], 0.75)
                        self.assertEqual(result["risk_tier"], "HIGH")

    async def test_handles_subprocess_failure(self) -> None:
        mock_resp = MagicMock()
        mock_resp.text = "def foo(): pass"

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal error"))

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                result = await github_app_analyzer.analyze_file("owner/repo", 1, "app.py", "sha", "token")
                self.assertIsNone(result)

    async def test_concurrent_isolation(self) -> None:
        mock_resp = MagicMock()
        mock_resp.text = "def foo(): pass"

        side_effects = [
            (b'{"unified_risk_score":0.1,"risk_tier":"LOW","security_findings":[],"per_function_metrics":[]}', b""),
            (b'{"unified_risk_score":0.9,"risk_tier":"HIGH","security_findings":[],"per_function_metrics":[]}', b""),
        ]

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(side_effect=side_effects)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                tasks = [
                    github_app_analyzer.analyze_file("owner/repo", 1, "a.py", "sha", "token"),
                    github_app_analyzer.analyze_file("owner/repo", 1, "b.py", "sha", "token"),
                ]
                results = await asyncio.gather(*tasks)
                self.assertEqual(results[0]["risk_score"], 0.1)
                self.assertEqual(results[1]["risk_score"], 0.9)

    def test_temp_directory_cleanup(self) -> None:
        tmpdir = tempfile.mkdtemp()

        mock_resp = MagicMock()
        mock_resp.text = "def foo(): pass"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                b'{"unified_risk_score":0.5,"risk_tier":"LOW","security_findings":[],"per_function_metrics":[]}',
                b"",
            )
        )

        with patch("tempfile.gettempdir", return_value=tmpdir):
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                    asyncio.run(github_app_analyzer.analyze_file("owner/repo", 1, "app.py", "sha", "token"))

        expected_path = Path(tmpdir) / "omni-auditor-owner-repo-1-app.py"
        self.assertFalse(expected_path.exists())


if __name__ == "__main__":
    unittest.main()
