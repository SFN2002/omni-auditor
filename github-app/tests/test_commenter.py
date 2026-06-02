"""Tests for github-app/commenter.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

github_app_commenter = __import__("github-app.commenter", fromlist=["post_pr_comment", "_format_comment"])


class TestFormatComment(unittest.TestCase):
    """Markdown comment body generation."""

    def test_markdown_table_generation(self) -> None:
        results = [
            {"file_path": "app.py", "risk_score": 0.85, "risk_tier": "HIGH", "findings_count": 3},
        ]
        comment = github_app_commenter._format_comment(results)
        self.assertIn("app.py", comment)
        self.assertIn("0.8500", comment)
        self.assertIn("HIGH", comment)
        self.assertIn("3", comment)
        self.assertIn("<!-- omni-auditor-github-app -->", comment)

    def test_drift_included(self) -> None:
        results = [
            {"file_path": "app.py", "risk_score": 0.85, "risk_tier": "HIGH", "findings_count": 3},
        ]
        drift = {"trend": "DEGRADED", "score": 0.45}
        comment = github_app_commenter._format_comment(results, drift)
        self.assertIn("DEGRADED", comment)
        self.assertIn("0.4500", comment)
        self.assertIn("↑", comment)


class TestPostPRComment(unittest.IsolatedAsyncioTestCase):
    """PR comment upsert logic."""

    async def test_creates_new_comment_when_no_marker(self) -> None:
        mock_comment = MagicMock()
        mock_comment.body = "Some other comment"

        mock_pr = MagicMock()
        mock_pr.get_issue_comments.return_value = [mock_comment]

        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        results = [{"file_path": "app.py", "risk_score": 0.5, "risk_tier": "MEDIUM", "findings_count": 0}]

        await github_app_commenter.post_pr_comment(mock_client, "owner/repo", 1, results)

        mock_pr.create_issue_comment.assert_called_once()
        mock_comment.edit.assert_not_called()

    async def test_updates_existing_comment_with_marker(self) -> None:
        mock_comment = MagicMock()
        mock_comment.body = "<!-- omni-auditor-github-app --> Old analysis"

        mock_pr = MagicMock()
        mock_pr.get_issue_comments.return_value = [mock_comment]

        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr

        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo

        results = [{"file_path": "app.py", "risk_score": 0.5, "risk_tier": "MEDIUM", "findings_count": 0}]

        await github_app_commenter.post_pr_comment(mock_client, "owner/repo", 1, results)

        mock_comment.edit.assert_called_once()
        mock_pr.create_issue_comment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
