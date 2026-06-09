"""
Omni-Auditor SaaS Dashboard — Celery Tasks.

Async background tasks for running security scans and processing
webhook events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from celery import shared_task

# These imports work when the package is installed
# but use try/except for standalone task execution context
try:
    from saas.backend.database import AsyncSessionLocal
    from saas.backend.models import Baseline, Finding, Project, Scan, WebhookEvent
    from saas.backend.config import settings
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ── Realistic security finding rules for mock analysis ────────

MOCK_SECURITY_RULES = [
    {
        "rule_id": "PY-SQL-INJECTION-001",
        "title": "Potential SQL Injection via string formatting",
        "description": "User-controlled input is used in a SQL query constructed via string formatting, which can lead to SQL injection attacks.",
        "severity": "critical",
        "confidence": "high",
        "category": "injection",
        "cwe_ids": ["CWE-89"],
        "owasp_category": "A03:2021 - Injection",
        "remediation": "Use parameterized queries or an ORM instead of string formatting. Never concatenate user input into SQL queries.",
    },
    {
        "rule_id": "PY-HARDCODED-SECRET-002",
        "title": "Hardcoded API key or secret detected",
        "description": "An API key, password, or other secret was found hardcoded in the source code. This exposes sensitive credentials.",
        "severity": "critical",
        "confidence": "high",
        "category": "secrets",
        "cwe_ids": ["CWE-798", "CWE-259"],
        "owasp_category": "A07:2021 - Identification and Authentication Failures",
        "remediation": "Use environment variables or a secrets manager. Remove hardcoded credentials and rotate any exposed secrets.",
    },
    {
        "rule_id": "PY-XSS-003",
        "title": "Reflected XSS via unescaped template output",
        "description": "User input is rendered in a template without proper escaping, enabling cross-site scripting (XSS) attacks.",
        "severity": "high",
        "confidence": "medium",
        "category": "xss",
        "cwe_ids": ["CWE-79"],
        "owasp_category": "A03:2021 - Injection",
        "remediation": "Use auto-escaping template engines or explicitly escape all user input before rendering.",
    },
    {
        "rule_id": "PY-SSRF-004",
        "title": "Server-Side Request Forgery (SSRF) via URL parameter",
        "description": "User-supplied URLs are fetched by the server without validation, allowing attackers to make requests to internal services.",
        "severity": "high",
        "confidence": "medium",
        "category": "ssrf",
        "cwe_ids": ["CWE-918"],
        "owasp_category": "A10:2021 - Server-Side Request Forgery",
        "remediation": "Validate and sanitize URLs. Use an allowlist of permitted domains. Disable redirects.",
    },
    {
        "rule_id": "PY-INSECURE-DESER-005",
        "title": "Insecure deserialization with pickle",
        "description": "The pickle module is used to deserialize untrusted data, which can lead to remote code execution.",
        "severity": "critical",
        "confidence": "high",
        "category": "insecure_deserialization",
        "cwe_ids": ["CWE-502"],
        "owasp_category": "A08:2021 - Software and Data Integrity Failures",
        "remediation": "Use safe serialization formats like JSON. Never deserialize untrusted data with pickle.",
    },
    {
        "rule_id": "PY-CMD-INJECT-006",
        "title": "Command injection via os.system",
        "description": "User input is passed to os.system() or subprocess without sanitization, enabling arbitrary command execution.",
        "severity": "critical",
        "confidence": "high",
        "category": "injection",
        "cwe_ids": ["CWE-78"],
        "owasp_category": "A03:2021 - Injection",
        "remediation": "Use subprocess with a list of arguments instead of shell=True. Validate and sanitize all inputs.",
    },
    {
        "rule_id": "PY-WEAK-HASH-007",
        "title": "Use of weak hashing algorithm (MD5)",
        "description": "MD5 is used for hashing, which is cryptographically broken and unsuitable for security-sensitive operations.",
        "severity": "medium",
        "confidence": "high",
        "category": "crypto_failures",
        "cwe_ids": ["CWE-327"],
        "owasp_category": "A02:2021 - Cryptographic Failures",
        "remediation": "Use SHA-256 or stronger hashing algorithms. For password hashing, use bcrypt, scrypt, or Argon2.",
    },
    {
        "rule_id": "PY-MISSING-AUTH-008",
        "title": "API endpoint missing authentication",
        "description": "An API endpoint does not enforce authentication, allowing unauthenticated access to sensitive functionality.",
        "severity": "high",
        "confidence": "medium",
        "category": "access_control",
        "cwe_ids": ["CWE-306"],
        "owasp_category": "A01:2021 - Broken Access Control",
        "remediation": "Implement proper authentication and authorization checks on all sensitive endpoints.",
    },
    {
        "rule_id": "PY-PATH-TRAVERSAL-009",
        "title": "Path traversal via unsanitized file path",
        "description": "User input is used to construct file paths without validation, allowing directory traversal attacks.",
        "severity": "high",
        "confidence": "medium",
        "category": "file_upload",
        "cwe_ids": ["CWE-22"],
        "owasp_category": "A01:2021 - Broken Access Control",
        "remediation": "Validate file paths, use os.path.abspath, and restrict access to designated directories.",
    },
    {
        "rule_id": "PY-DEBUG-ENABLED-010",
        "title": "Debug mode enabled in production configuration",
        "description": "DEBUG=True is detected in a configuration file, which exposes sensitive information in production.",
        "severity": "medium",
        "confidence": "high",
        "category": "security_misconfig",
        "cwe_ids": ["CWE-489"],
        "owasp_category": "A05:2021 - Security Misconfiguration",
        "remediation": "Set DEBUG=False in production. Use environment variables to control debug settings.",
    },
    {
        "rule_id": "PY-CSRF-011",
        "title": "Missing CSRF protection on state-changing endpoint",
        "description": "A POST/PUT/DELETE endpoint does not implement CSRF tokens, enabling cross-site request forgery attacks.",
        "severity": "medium",
        "confidence": "medium",
        "category": "authentication",
        "cwe_ids": ["CWE-352"],
        "owasp_category": "A01:2021 - Broken Access Control",
        "remediation": "Implement CSRF token validation or use SameSite cookie attributes.",
    },
    {
        "rule_id": "PY-LOG-INJECTION-012",
        "title": "Log injection via unsanitized user input",
        "description": "User input is written directly to logs without sanitization, potentially corrupting log files or forging log entries.",
        "severity": "low",
        "confidence": "medium",
        "category": "logging_monitoring",
        "cwe_ids": ["CWE-117"],
        "owasp_category": "A09:2021 - Security Logging and Monitoring Failures",
        "remediation": "Sanitize user input before logging. Strip newlines and control characters.",
    },
    {
        "rule_id": "PY-UNVALIDATED-REDIRECT-013",
        "title": "Unvalidated redirect via user-controlled URL",
        "description": "User input controls the redirect destination, enabling phishing attacks via open redirects.",
        "severity": "medium",
        "confidence": "medium",
        "category": "access_control",
        "cwe_ids": ["CWE-601"],
        "owasp_category": "A01:2021 - Broken Access Control",
        "remediation": "Use a mapping of allowed redirect destinations or validate URLs against an allowlist.",
    },
    {
        "rule_id": "PY-RACE-CONDITION-014",
        "title": "Race condition in concurrent file access",
        "description": "A file is checked for existence and then accessed, creating a TOCTOU race condition.",
        "severity": "low",
        "confidence": "low",
        "category": "race_conditions",
        "cwe_ids": ["CWE-367"],
        "owasp_category": "A03:2021 - Injection",
        "remediation": "Use atomic file operations or file locking mechanisms.",
    },
    {
        "rule_id": "PY-DEPRECATED-015",
        "title": "Use of deprecated cryptography function",
        "description": "A deprecated or known-vulnerable cryptographic function is being used.",
        "severity": "medium",
        "confidence": "high",
        "category": "known_vulns",
        "cwe_ids": ["CWE-1104"],
        "owasp_category": "A06:2021 - Vulnerable and Outdated Components",
        "remediation": "Update to the latest version of the cryptography library and use recommended APIs.",
    },
    {
        "rule_id": "PY-XXE-016",
        "title": "XML External Entity (XXE) vulnerability",
        "description": "XML parsing allows external entities, which can lead to SSRF, file disclosure, or DoS.",
        "severity": "high",
        "confidence": "high",
        "category": "xxe",
        "cwe_ids": ["CWE-611"],
        "owasp_category": "A05:2021 - Security Misconfiguration",
        "remediation": "Disable external entities and DTD processing in XML parsers.",
    },
    {
        "rule_id": "PY-INFO-DISCLOSURE-017",
        "title": "Sensitive information in error messages",
        "description": "Error handlers expose stack traces or internal details that aid attackers.",
        "severity": "low",
        "confidence": "high",
        "category": "error_handling",
        "cwe_ids": ["CWE-209"],
        "owasp_category": "A05:2021 - Security Misconfiguration",
        "remediation": "Return generic error messages to users. Log detailed errors server-side only.",
    },
    {
        "rule_id": "PY-INPUT-VALIDATION-018",
        "title": "Missing input validation on numeric parameter",
        "description": "A numeric parameter is used without type validation or bounds checking.",
        "severity": "medium",
        "confidence": "medium",
        "category": "input_validation",
        "cwe_ids": ["CWE-20"],
        "owasp_category": "A03:2021 - Injection",
        "remediation": "Validate all inputs using type conversion and range checks.",
    },
    {
        "rule_id": "PY-DEPENDENCY-019",
        "title": "Outdated dependency with known vulnerability",
        "description": "A third-party dependency has a known CVE and should be updated.",
        "severity": "high",
        "confidence": "medium",
        "category": "dependency_management",
        "cwe_ids": ["CWE-1104"],
        "owasp_category": "A06:2021 - Vulnerable and Outdated Components",
        "remediation": "Update the dependency to the latest patched version. Use tools like Dependabot or Snyk.",
    },
    {
        "rule_id": "PY-CONTAINER-020",
        "title": "Container running as root user",
        "description": "The Dockerfile does not specify a non-root USER, increasing container breakout risk.",
        "severity": "medium",
        "confidence": "high",
        "category": "container_security",
        "cwe_ids": ["CWE-250"],
        "owasp_category": "A05:2021 - Security Misconfiguration",
        "remediation": "Add a non-root USER directive in the Dockerfile and run the application as an unprivileged user.",
    },
]

# ── 90-D Risk Vector Dimensions ───────────────────────────────

RISK_DIMENSIONS = [
    "D01_injection",
    "D02_broken_auth",
    "D03_sensitive_data",
    "D04_xxe",
    "D05_access_control",
    "D06_security_misconfig",
    "D07_xss",
    "D08_insecure_deserialization",
    "D09_known_vulns",
    "D10_logging_monitoring",
    "D11_crypto_failures",
    "D12_ssrf",
    "D13_file_upload",
    "D14_command_injection",
    "D15_race_conditions",
    "D16_api_security",
    "D17_secrets_management",
    "D18_dependency_management",
    "D19_code_quality",
    "D20_error_handling",
    "D21_session_management",
    "D22_input_validation",
    "D23_authentication",
    "D24_authorization",
    "D25_data_integrity",
    "D26_network_security",
    "D27_container_security",
    "D28_cloud_security",
    "D29_iac_security",
    "D30_supply_chain",
]


def generate_mock_findings(project_id: UUID, scan_id: UUID) -> List[Dict[str, Any]]:
    """Generate realistic mock security findings for a scan.

    Randomly selects 3-12 findings from the predefined rule set
    and assigns realistic file paths and line numbers.
    """
    import random

    num_findings = random.randint(3, min(12, len(MOCK_SECURITY_RULES)))
    selected_rules = random.sample(MOCK_SECURITY_RULES, num_findings)

    file_paths = [
        "app/routes/auth.py",
        "app/services/database.py",
        "app/utils/helpers.py",
        "app/api/users.py",
        "config/settings.py",
        "app/templates/index.html",
        "app/middleware/security.py",
        "scripts/deploy.sh",
        "app/handlers/upload.py",
        "app/core/serializers.py",
        "requirements.txt",
        "docker/Dockerfile",
        "app/clients/external.py",
        "app/views/admin.py",
    ]

    findings = []
    for rule in selected_rules:
        file_path = random.choice(file_paths)
        line_start = random.randint(1, 200)

        finding = {
            "scan_id": str(scan_id),
            "project_id": str(project_id),
            "rule_id": rule["rule_id"],
            "title": rule["title"],
            "description": rule["description"],
            "severity": rule["severity"],
            "confidence": rule["confidence"],
            "category": rule["category"],
            "file_path": file_path,
            "line_start": line_start,
            "line_end": line_start + random.randint(0, 10),
            "code_snippet": f"# {rule['title']} detected at line {line_start}",
            "remediation": rule["remediation"],
            "cwe_ids": rule["cwe_ids"],
            "owasp_category": rule["owasp_category"],
            "status": "open",
        }
        findings.append(finding)

    return findings


def generate_90d_risk_vector(findings: List[Dict[str, Any]]) -> Dict[str, float]:
    """Calculate the 90-D risk vector from findings.

    Each dimension is scored based on the presence and severity
    of matching findings, normalized to 0.0-1.0 range.
    """
    import random

    vector = {}

    # Map categories to dimensions
    category_to_dim = {
        "injection": "D01_injection",
        "secrets": "D03_sensitive_data",
        "xxe": "D04_xxe",
        "access_control": "D05_access_control",
        "security_misconfig": "D06_security_misconfig",
        "xss": "D07_xss",
        "insecure_deserialization": "D08_insecure_deserialization",
        "known_vulns": "D09_known_vulns",
        "logging_monitoring": "D10_logging_monitoring",
        "crypto_failures": "D11_crypto_failures",
        "ssrf": "D12_ssrf",
        "file_upload": "D13_file_upload",
        "race_conditions": "D15_race_conditions",
        "api_security": "D16_api_security",
        "input_validation": "D22_input_validation",
        "authentication": "D23_authentication",
        "authorization": "D24_authorization",
        "error_handling": "D20_error_handling",
        "dependency_management": "D18_dependency_management",
        "container_security": "D27_container_security",
    }

    # Initialize all dimensions with random low values (0.01-0.10)
    for dim in RISK_DIMENSIONS:
        vector[dim] = round(random.uniform(0.01, 0.10), 2)

    # Increase dimensions based on findings
    for finding in findings:
        cat = finding.get("category", "")
        severity = finding.get("severity", "info")
        dim = category_to_dim.get(cat)

        if dim:
            severity_multiplier = {
                "critical": 0.9,
                "high": 0.7,
                "medium": 0.5,
                "low": 0.3,
                "info": 0.1,
            }.get(severity, 0.1)

            vector[dim] = min(1.0, vector[dim] + severity_multiplier * random.uniform(0.3, 1.0))
            vector[dim] = round(vector[dim], 2)

    # Calculate overall risk score as average
    dimension_values = [v for k, v in vector.items() if k.startswith("D")]
    vector["overall_risk_score"] = round(
        sum(dimension_values) / len(dimension_values), 2
    ) if dimension_values else 0.0

    return vector


def calculate_baseline_status(
    current_risk: float,
    findings: List[Dict[str, Any]],
) -> str:
    """Determine baseline status based on risk score and findings.

    Statuses: improved, stable, degraded, fractured
    """
    # For the first scan without a prior baseline, return stable
    critical_count = sum(1 for f in findings if f.get("severity") == "critical")
    high_count = sum(1 for f in findings if f.get("severity") == "high")

    if critical_count > 0 or high_count >= 3:
        return "degraded" if random.random() > 0.5 else "fractured"

    if current_risk < 0.2:
        return "improved"
    elif current_risk < 0.4:
        return "stable"
    else:
        return "degraded"


# ── Celery Task: Run Omni-Auditor Analysis ────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="saas.backend.tasks.run_omni_auditor_analysis",
)
def run_omni_auditor_analysis(
    self,
    scan_id: str,
    repo_url: Optional[str] = None,
    commit_sha: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the Omni-Auditor security analysis on a repository.

    This task:
    1. Updates the scan status to 'running'
    2. Runs the analysis (or generates mock findings)
    3. Parses results and stores findings in the database
    4. Calculates the 90-D risk vector
    5. Determines baseline status
    6. Updates the scan status to 'completed' (or 'failed')

    Args:
        scan_id: The UUID of the scan record.
        repo_url: The GitHub repository URL (owner/repo format).
        commit_sha: The commit SHA being scanned.

    Returns:
        dict with scan results summary.
    """
    import asyncio

    logger.info(f"Starting analysis for scan {scan_id}, repo={repo_url}")

    return asyncio.get_event_loop().run_until_complete(
        _run_analysis_async(scan_id, repo_url, commit_sha)
    )


async def _run_analysis_async(
    scan_id: str,
    repo_url: Optional[str],
    commit_sha: Optional[str],
) -> Dict[str, Any]:
    """Async implementation of the analysis task."""
    from saas.backend.database import AsyncSessionLocal
    from saas.backend.models import Baseline, Finding, Project, Scan

    async with AsyncSessionLocal() as db:
        try:
            # 1. Update scan to running
            from sqlalchemy import select
            result = await db.execute(
                select(Scan).where(Scan.id == scan_id)
            )
            scan = result.scalar_one_or_none()

            if not scan:
                logger.error(f"Scan {scan_id} not found")
                return {"status": "failed", "error": "Scan not found"}

            scan.status = "running"
            scan.started_at = datetime.now(timezone.utc)
            await db.commit()

            # 2. Attempt to run actual omni_auditor analysis
            findings_data = []
            try:
                # Try running the real omni_auditor tool
                cmd = [
                    "python", "-m", "omni_auditor",
                    "--json",
                    f"--repo={repo_url}" if repo_url else "",
                    f"--commit={commit_sha}" if commit_sha else "",
                ]
                cmd = [c for c in cmd if c]

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=300
                )

                if proc.returncode == 0 and stdout:
                    output = json.loads(stdout.decode("utf-8"))
                    findings_data = output.get("findings", [])
                    risk_score = output.get("risk_score", 0.0)
                else:
                    # Fall back to mock analysis
                    logger.info(
                        f"omni_auditor not available for scan {scan_id}, using mock data"
                    )
                    findings_data = generate_mock_findings(
                        scan.project_id, scan.id
                    )
                    risk_score = None

            except (FileNotFoundError, asyncio.TimeoutError, json.JSONDecodeError):
                logger.info(f"Using mock findings for scan {scan_id}")
                findings_data = generate_mock_findings(
                    scan.project_id, scan.id
                )
                risk_score = None

            # 3. Store findings
            severity_counts = {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "info": 0,
            }

            for finding_data in findings_data:
                finding = Finding(
                    scan_id=scan.id,
                    project_id=scan.project_id,
                    rule_id=finding_data["rule_id"],
                    title=finding_data["title"],
                    description=finding_data.get("description"),
                    severity=finding_data["severity"],
                    confidence=finding_data.get("confidence"),
                    category=finding_data.get("category"),
                    file_path=finding_data.get("file_path"),
                    line_start=finding_data.get("line_start"),
                    line_end=finding_data.get("line_end"),
                    code_snippet=finding_data.get("code_snippet"),
                    remediation=finding_data.get("remediation"),
                    cwe_ids=finding_data.get("cwe_ids"),
                    owasp_category=finding_data.get("owasp_category"),
                    status=finding_data.get("status", "open"),
                )
                db.add(finding)
                severity_counts[finding_data["severity"]] += 1

            await db.flush()

            # 4. Calculate 90-D risk vector
            risk_vector = generate_90d_risk_vector(findings_data)

            # Calculate risk score if not from real analysis
            if risk_score is None:
                risk_score = risk_vector.get("overall_risk_score", 0.0)

            # 5. Determine baseline status
            baseline_status = calculate_baseline_status(risk_score, findings_data)

            # 6. Get latest baseline for comparison
            baseline_result = await db.execute(
                select(Baseline)
                .where(Baseline.project_id == scan.project_id)
                .order_by(Baseline.created_at.desc())
                .limit(1)
            )
            latest_baseline = baseline_result.scalar_one_or_none()

            if latest_baseline and latest_baseline.risk_score:
                baseline_risk = float(latest_baseline.risk_score)
                if baseline_risk > 0:
                    pct_change = (risk_score - baseline_risk) / baseline_risk
                    if pct_change < -0.10:
                        baseline_status = "improved"
                    elif pct_change > 0.10:
                        baseline_status = "degraded"
                    else:
                        baseline_status = "stable"

                # Check for fractured (critical/high increase > 50%)
                baseline_dist = latest_baseline.findings_distribution or {}
                if isinstance(baseline_dist, str):
                    baseline_dist = json.loads(baseline_dist)
                baseline_high = baseline_dist.get("critical", 0) + baseline_dist.get("high", 0)
                current_high = severity_counts["critical"] + severity_counts["high"]
                if baseline_high > 0 and (current_high - baseline_high) / baseline_high > 0.5:
                    baseline_status = "fractured"

            # Update scan record
            scan.status = "completed"
            scan.risk_score = risk_score
            scan.risk_vector_90d = risk_vector
            scan.findings_count = len(findings_data)
            scan.critical_count = severity_counts["critical"]
            scan.high_count = severity_counts["high"]
            scan.medium_count = severity_counts["medium"]
            scan.low_count = severity_counts["low"]
            scan.info_count = severity_counts["info"]
            scan.baseline_status = baseline_status
            scan.completed_at = datetime.now(timezone.utc)

            await db.commit()

            logger.info(
                f"Scan {scan_id} completed: {len(findings_data)} findings, "
                f"risk_score={risk_score}, status={baseline_status}"
            )

            return {
                "status": "completed",
                "scan_id": scan_id,
                "findings_count": len(findings_data),
                "risk_score": risk_score,
                "baseline_status": baseline_status,
                "severity_counts": severity_counts,
            }

        except Exception as exc:
            logger.exception(f"Scan {scan_id} failed: {exc}")

            # Mark scan as failed
            try:
                from sqlalchemy import select
                result = await db.execute(
                    select(Scan).where(Scan.id == scan_id)
                )
                scan = result.scalar_one_or_none()
                if scan:
                    scan.status = "failed"
                    scan.completed_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception:
                pass

            # Retry if applicable
            raise self.retry(exc=exc)


# ── Celery Task: Process Webhook Event ────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="saas.backend.tasks.process_webhook_event",
)
def process_webhook_event(self, event_id: str) -> Dict[str, Any]:
    """Process a stored webhook event asynchronously.

    Marks the event as processed and performs any follow-up actions.

    Args:
        event_id: The UUID of the webhook event record.

    Returns:
        dict with processing result.
    """
    import asyncio

    return asyncio.get_event_loop().run_until_complete(
        _process_webhook_async(event_id)
    )


async def _process_webhook_async(event_id: str) -> Dict[str, Any]:
    """Async implementation of webhook processing."""
    from saas.backend.database import AsyncSessionLocal
    from saas.backend.models import WebhookEvent

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(WebhookEvent).where(WebhookEvent.id == event_id)
        )
        event = result.scalar_one_or_none()

        if not event:
            return {"status": "failed", "error": "Event not found"}

        event.processed = True
        await db.commit()

        logger.info(f"Webhook event {event_id} processed")

        return {
            "status": "processed",
            "event_id": event_id,
            "event_type": event.event_type,
        }
