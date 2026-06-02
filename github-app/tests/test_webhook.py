"""Tests for github-app/main.py webhook handler."""

from __future__ import annotations

import hmac
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# importlib handles the hyphenated package name via fromlist
github_app_main = __import__("github-app.main", fromlist=["app", "_verify_signature", "webhook", "settings"])


class TestWebhookSignature(unittest.TestCase):
    """HMAC-SHA256 signature verification."""

    def test_valid_signature(self) -> None:
        secret = "test-secret"
        body = b'{"action":"opened"}'
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.object(github_app_main.settings, "webhook_secret", secret):
            result = github_app_main._verify_signature(body, expected)
            self.assertTrue(result)

    def test_invalid_signature(self) -> None:
        secret = "test-secret"
        body = b'{"action":"opened"}'

        with patch.object(github_app_main.settings, "webhook_secret", secret):
            result = github_app_main._verify_signature(body, "sha256=invalid")
            self.assertFalse(result)

    def test_missing_signature(self) -> None:
        body = b'{"action":"opened"}'
        result = github_app_main._verify_signature(body, None)
        self.assertFalse(result)


class TestWebhookRoutes(unittest.IsolatedAsyncioTestCase):
    """FastAPI route handlers."""

    async def test_pull_request_opened_event(self) -> None:
        payload = {
            "action": "opened",
            "pull_request": {"number": 42, "head": {"sha": "abc123"}},
            "repository": {"full_name": "owner/repo"},
            "installation": {"id": 123},
        }
        body = json.dumps(payload).encode()
        secret = "test-secret"
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        request = MagicMock()
        request.body = AsyncMock(return_value=body)
        request.headers = {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "pull_request",
        }

        mock_install_auth = MagicMock()
        mock_install_auth.token = "token123"

        with patch.object(github_app_main.settings, "webhook_secret", secret):
            with patch.object(github_app_main, "_get_installation_client", new_callable=AsyncMock):
                with patch.object(
                    github_app_main,
                    "_get_changed_python_files",
                    new_callable=AsyncMock,
                    return_value=[{"filename": "app.py", "status": "modified", "raw_url": "http://example.com/app.py"}],
                ):
                    with patch.object(
                        github_app_main, "_get_installation_auth", new_callable=AsyncMock, return_value=mock_install_auth
                    ):
                        with patch.object(
                            github_app_main,
                            "analyze_file",
                            new_callable=AsyncMock,
                            return_value={
                                "file_path": "app.py",
                                "risk_score": 0.5,
                                "risk_tier": "MEDIUM",
                                "findings_count": 0,
                                "function_metrics": [],
                                "security_findings": [],
                                "raw_report": {},
                            },
                        ):
                            with patch.object(github_app_main, "compute_drift", return_value=None):
                                with patch.object(github_app_main, "post_pr_comment", new_callable=AsyncMock):
                                    response = await github_app_main.webhook(request)
                                    self.assertEqual(response.status_code, 200)

    async def test_unknown_event_type(self) -> None:
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        request.headers = {
            "X-Hub-Signature-256": "sha256=abc",
            "X-GitHub-Event": "push",
        }

        with patch.object(github_app_main.settings, "webhook_secret", "test-secret"):
            with patch.object(github_app_main, "_verify_signature", return_value=True):
                response = await github_app_main.webhook(request)
                self.assertEqual(response.status_code, 204)

    async def test_invalid_signature(self) -> None:
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        request.headers = {
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Event": "pull_request",
        }

        with patch.object(github_app_main.settings, "webhook_secret", "test-secret"):
            response = await github_app_main.webhook(request)
            self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
