"""Post or update PR comments with Omni-Auditor results."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from github import Github
from github.PullRequest import PullRequest

logger = logging.getLogger(__name__)

_COMMENT_MARKER = "<!-- omni-auditor-github-app -->"


def _tier_emoji(tier: str) -> str:
    return {
        "CRITICAL": "🔴",
        "HIGH": "🟠",
        "MEDIUM": "🟡",
        "LOW": "🟢",
    }.get(tier.upper(), "⚪")


def _format_comment(
    results: list[dict[str, Any]],
    drift: dict[str, Any] | None = None,
) -> str:
    """Build a Markdown comment body."""
    lines: list[str] = [
        "## 🛡️ Omni-Auditor Analysis",
        "",
        "| File | Risk Score | Tier | Findings |",
        "|------|-----------|------|----------|",
    ]

    for r in results:
        tier = r.get("risk_tier", "UNKNOWN")
        emoji = _tier_emoji(tier)
        lines.append(
            f"| `{r['file_path']}` | {r['risk_score']:.4f} | {emoji} {tier} | {r['findings_count']} |"
        )

    if drift:
        trend = drift.get("trend", "UNKNOWN")
        score = drift.get("score", 0.0)
        arrow = "↑" if trend in ("DEGRADED", "FRACTURED") else "↓" if trend == "IMPROVED" else "→"
        lines.append("")
        lines.append(f"**Drift:** {trend} {arrow} {score:.4f} from baseline")

    lines.append("")
    lines.append(_COMMENT_MARKER)
    return "\n".join(lines)


async def post_pr_comment(
    github_client: Github,
    repo_name: str,
    pr_number: int,
    results: list[dict[str, Any]],
    drift: dict[str, Any] | None = None,
) -> None:
    """Create or update the Omni-Auditor comment on a PR.

    Uses the ``_COMMENT_MARKER`` to identify an existing comment so we
    don't spam the PR with duplicate comments on every push.
    """
    try:
        repo = await asyncio.to_thread(github_client.get_repo, repo_name)
        pr: PullRequest = await asyncio.to_thread(repo.get_pull, pr_number)

        # Look for an existing comment with our marker
        existing_comment = None
        comments = await asyncio.to_thread(list, pr.get_issue_comments())
        for comment in comments:
            if _COMMENT_MARKER in comment.body:
                existing_comment = comment
                break

        body = _format_comment(results, drift)

        if existing_comment:
            await asyncio.to_thread(existing_comment.edit, body)
            logger.info("Updated existing PR comment for %s#%d", repo_name, pr_number)
        else:
            await asyncio.to_thread(pr.create_issue_comment, body)
            logger.info("Created new PR comment for %s#%d", repo_name, pr_number)

    except Exception as exc:
        logger.error("Failed to post PR comment for %s#%d: %s", repo_name, pr_number, exc)
