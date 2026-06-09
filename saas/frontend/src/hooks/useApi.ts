import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { Project, Scan, Finding, DashboardStats, RiskTrendPoint, Severity, BaselineStatus } from '../types';

// ---- Mock Data ----

const MOCK_PROJECTS: Project[] = [
  {
    id: 'proj-1', name: 'web-api', slug: 'web-api',
    description: 'Main REST API backend service built with Python/FastAPI',
    github_repo: 'acme/web-api', risk_score: 2.4,
    last_scan_at: new Date(Date.now() - 120000).toISOString(),
    findings_count: 8, scans_count: 52, baseline_status: 'IMPROVED',
  },
  {
    id: 'proj-2', name: 'mobile-app', slug: 'mobile-app',
    description: 'Mobile API gateway and backend for iOS/Android apps',
    github_repo: 'acme/mobile-app', risk_score: 6.7,
    last_scan_at: new Date(Date.now() - 3600000).toISOString(),
    findings_count: 24, scans_count: 38, baseline_status: 'DEGRADED',
  },
  {
    id: 'proj-3', name: 'ml-service', slug: 'ml-service',
    description: 'Machine learning inference pipeline and model serving',
    github_repo: 'acme/ml-service', risk_score: 4.5,
    last_scan_at: new Date(Date.now() - 7200000).toISOString(),
    findings_count: 12, scans_count: 29, baseline_status: 'STABLE',
  },
];

const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];

const SCAN_TEMPLATES = [
  { status: 'completed' as const, branch: 'main', triggered_by: 'sarah@acme.com', risk_score: 8.1, c: 3, h: 5, m: 8, l: 4, i: 2 },
  { status: 'completed' as const, branch: 'main', triggered_by: 'ci-cd', risk_score: 7.8, c: 2, h: 6, m: 7, l: 3, i: 1 },
  { status: 'completed' as const, branch: 'main', triggered_by: 'ci-cd', risk_score: 7.5, c: 2, h: 5, m: 6, l: 4, i: 2 },
  { status: 'failed' as const, branch: 'feature/auth', triggered_by: 'mike@acme.com', risk_score: undefined, c: 0, h: 0, m: 0, l: 0, i: 0 },
  { status: 'completed' as const, branch: 'main', triggered_by: 'ci-cd', risk_score: 7.1, c: 1, h: 5, m: 7, l: 3, i: 0 },
  { status: 'completed' as const, branch: 'main', triggered_by: 'sarah@acme.com', risk_score: 6.8, c: 1, h: 4, m: 5, l: 2, i: 0 },
  { status: 'completed' as const, branch: 'main', triggered_by: 'ci-cd', risk_score: 6.2, c: 1, h: 3, m: 6, l: 2, i: 1 },
  { status: 'completed' as const, branch: 'dev', triggered_by: 'alex@acme.com', risk_score: 5.8, c: 0, h: 4, m: 5, l: 3, i: 2 },
  { status: 'completed' as const, branch: 'main', triggered_by: 'ci-cd', risk_score: 4.2, c: 0, h: 2, m: 4, l: 2, i: 0 },
  { status: 'completed' as const, branch: 'main', triggered_by: 'ci-cd', risk_score: 3.1, c: 0, h: 1, m: 3, l: 1, i: 1 },
];

function makeScans(projectId: string, offset = 0): Scan[] {
  const templates = offset % 2 === 0 ? SCAN_TEMPLATES : [...SCAN_TEMPLATES].reverse();
  return templates.map((t, i) => ({
    id: `scan-${projectId}-${offset + i}`,
    project_id: projectId,
    status: t.status,
    commit_sha: `a1b2c3d${i}e${i}f${i}`.slice(0, 8),
    branch: t.branch,
    risk_score: t.risk_score,
    risk_vector_90d: {
      'SQL Injection': Math.round((Math.random() * 4 + 5) * 10) / 10,
      'XSS': Math.round((Math.random() * 4 + 3) * 10) / 10,
      'Command Injection': Math.round((Math.random() * 5 + 2) * 10) / 10,
      'Secrets Mgmt': Math.round((Math.random() * 4 + 4) * 10) / 10,
      'Access Control': Math.round((Math.random() * 5 + 2) * 10) / 10,
      'Crypto Failures': Math.round((Math.random() * 3 + 2) * 10) / 10,
    },
    findings_count: t.c + t.h + t.m + t.l + t.i,
    critical_count: t.c,
    high_count: t.h,
    medium_count: t.m,
    low_count: t.l,
    info_count: t.i,
    baseline_status: i === 0 ? 'STABLE' : i < 3 ? 'IMPROVED' : 'STABLE',
    triggered_by: t.triggered_by,
    started_at: new Date(Date.now() - (i + 1) * 86400000 - Math.random() * 3600000).toISOString(),
    completed_at: t.status === 'completed' ? new Date(Date.now() - i * 86400000 - Math.random() * 1800000).toISOString() : undefined,
    created_at: new Date(Date.now() - (i + 1) * 86400000).toISOString(),
  }));
}

const ALL_SCANS = MOCK_PROJECTS.flatMap((p) => makeScans(p.id));

const RISK_TREND: RiskTrendPoint[] = Array.from({ length: 15 }, (_, i) => {
  const date = new Date();
  date.setDate(date.getDate() - (14 - i));
  return {
    date: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    risk_score: Math.round((6.5 + Math.sin(i * 0.5) * 2 + Math.random() * 1.5) * 10) / 10,
    scan_id: `scan-trend-${i}`,
    commit_sha: `abc${i}def${i}`.slice(0, 7),
  };
});

const FINDING_TEMPLATES: Omit<Finding, 'id' | 'scan_id' | 'project_id' | 'created_at'>[] = [
  { rule_id: 'SQL-001', title: 'Raw SQL in user query', description: 'User input used directly in SQL query without parameterization', severity: 'critical', confidence: 'high', category: 'SQL Injection', file_path: 'payments/views.py', line_start: 142, line_end: 145, code_snippet: 'cursor.execute(f"SELECT * FROM payments WHERE id = {user_id}")', remediation: 'Use parameterized queries: cursor.execute("SELECT * FROM payments WHERE id = %s", (user_id,))', cwe_ids: ['CWE-89'], status: 'open' },
  { rule_id: 'CMD-002', title: 'Command injection via subprocess', description: 'subprocess.call() with unsanitized user input', severity: 'critical', confidence: 'high', category: 'Command Injection', file_path: 'scripts/backup.sh', line_start: 8, line_end: 10, code_snippet: 'subprocess.call(f"tar -czf {request.GET[\'file\']}", shell=True)', remediation: 'Use subprocess.run with shell=False and a list of arguments', cwe_ids: ['CWE-78'], status: 'open' },
  { rule_id: 'XSS-001', title: 'Reflected XSS in template', description: 'Template variable rendered without escape filter', severity: 'high', confidence: 'high', category: 'XSS', file_path: 'templates/invoice.html', line_start: 12, line_end: 14, code_snippet: '<div>{{ user_input | safe }}</div>', remediation: 'Remove the |safe filter or use auto-escaping', cwe_ids: ['CWE-79'], status: 'open' },
  { rule_id: 'PATH-001', title: 'Path traversal vulnerability', description: 'File path constructed from user input without sanitization', severity: 'high', confidence: 'high', category: 'Path Traversal', file_path: 'uploads/handlers.py', line_start: 89, line_end: 92, code_snippet: 'with open(f"/app/media/{request.GET[\'filename\']}", "r") as f:', remediation: 'Use os.path.abspath and check the resolved path is within the allowed directory', cwe_ids: ['CWE-22'], status: 'open' },
  { rule_id: 'KEY-001', title: 'Hardcoded API key', description: 'API key exposed in source code', severity: 'high', confidence: 'high', category: 'Secrets Mgmt', file_path: 'config/settings.py', line_start: 23, line_end: 25, code_snippet: 'STRIPE_API_KEY = "sk_live_abc123def456"', remediation: 'Store secrets in environment variables or a secrets manager', cwe_ids: ['CWE-798'], status: 'open' },
  { rule_id: 'HASH-001', title: 'Weak hash algorithm', description: 'MD5 used for password hashing', severity: 'high', confidence: 'medium', category: 'Crypto Failures', file_path: 'auth/utils.py', line_start: 56, line_end: 58, code_snippet: 'hashlib.md5(password.encode()).hexdigest()', remediation: 'Use bcrypt, argon2, or scrypt for password hashing', cwe_ids: ['CWE-327'], status: 'fixed' },
  { rule_id: 'INJ-003', title: 'Insecure deserialization', description: 'pickle.loads() on untrusted data', severity: 'medium', confidence: 'high', category: 'Insecure Deserialization', file_path: 'api/serializers.py', line_start: 67, line_end: 69, code_snippet: 'data = pickle.loads(request.body)', remediation: 'Use JSON serialization instead of pickle for user input', cwe_ids: ['CWE-502'], status: 'open' },
  { rule_id: 'AUTH-002', title: 'Missing MFA on admin endpoint', description: 'Admin endpoint lacks MFA requirement', severity: 'medium', confidence: 'medium', category: 'Access Control', file_path: 'auth/views.py', line_start: 134, line_end: 138, code_snippet: '@admin_required\ndef delete_user(request):\n    ...', remediation: 'Add @mfa_required decorator to admin endpoints', cwe_ids: ['CWE-306'], status: 'open' },
  { rule_id: 'LEAK-001', title: 'Sensitive data in logs', description: 'Sensitive data logged in plain text', severity: 'medium', confidence: 'medium', category: 'Info Disclosure', file_path: 'utils/logger.py', line_start: 45, line_end: 47, code_snippet: 'logger.info(f"Payment processed: {credit_card_number}")', remediation: 'Redact sensitive fields before logging', cwe_ids: ['CWE-532'], status: 'open' },
  { rule_id: 'CERT-001', title: 'Weak TLS version', description: 'TLS 1.0 still enabled', severity: 'low', confidence: 'high', category: 'Crypto Failures', file_path: 'config/nginx.conf', line_start: 12, line_end: 14, code_snippet: 'ssl_protocols TLSv1 TLSv1.1 TLSv1.2;', remediation: 'Remove TLSv1 and TLSv1.1, keep only TLSv1.2+', cwe_ids: ['CWE-326'], status: 'open' },
  { rule_id: 'HEAD-001', title: 'Missing CSP header', description: 'Content-Security-Policy header missing', severity: 'low', confidence: 'medium', category: 'Access Control', file_path: 'config/headers.py', line_start: 8, line_end: 10, code_snippet: 'response["X-Frame-Options"] = "SAMEORIGIN"', remediation: 'Add Content-Security-Policy header', cwe_ids: ['CWE-693'], status: 'open' },
  { rule_id: 'DEP-001', title: 'End-of-life dependency', description: 'django 3.2 reached end-of-life', severity: 'info', confidence: 'high', category: 'Dependencies', file_path: 'requirements.txt', line_start: 3, line_end: 3, code_snippet: 'django==3.2.15', remediation: 'Upgrade to Django 4.2 LTS or newer', cwe_ids: ['CWE-1104'], status: 'open' },
];

function makeFindings(scanId: string, projectId: string): Finding[] {
  const extra: typeof FINDING_TEMPLATES = [
    { rule_id: 'SQL-002', title: 'String concatenation in SQL', description: 'String concatenation used to build SQL query', severity: 'high', confidence: 'high', category: 'SQL Injection', file_path: 'analytics/reports.py', line_start: 34, code_snippet: 'query = "SELECT * FROM " + table_name', remediation: 'Use ORM or parameterized queries', cwe_ids: ['CWE-89'], status: 'open' },
    { rule_id: 'XSS-002', title: 'DOM-based XSS', description: 'User input written to DOM without sanitization', severity: 'high', confidence: 'medium', category: 'XSS', file_path: 'static/js/app.js', line_start: 56, code_snippet: 'document.innerHTML = userInput;', remediation: 'Use textContent instead or sanitize with DOMPurify', cwe_ids: ['CWE-79'], status: 'open' },
    { rule_id: 'SSRF-001', title: 'SSRF via URL parameter', description: 'Server-Side Request Forgery through URL parameter', severity: 'critical', confidence: 'high', category: 'SSRF', file_path: 'webhooks/proxy.py', line_start: 23, code_snippet: 'requests.get(request.GET["url"])', remediation: 'Validate URLs against allowlist and block internal IPs', cwe_ids: ['CWE-918'], status: 'open' },
    { rule_id: 'CRYPTO-001', title: 'Weak random number generator', description: 'random used instead of secrets for security context', severity: 'medium', confidence: 'medium', category: 'Crypto Failures', file_path: 'auth/tokens.py', line_start: 12, code_snippet: 'token = str(random.randint(100000, 999999))', remediation: 'Use secrets.token_urlsafe or secrets.randbelow', cwe_ids: ['CWE-338'], status: 'open' },
    { rule_id: 'AUTH-003', title: 'Missing rate limiting', description: 'Login endpoint has no rate limiting', severity: 'medium', confidence: 'high', category: 'Access Control', file_path: 'auth/views.py', line_start: 89, code_snippet: 'def login(request):\n    ...', remediation: 'Add @ratelimit decorator or use Django Ratelimit', cwe_ids: ['CWE-307'], status: 'open' },
    { rule_id: 'CSRF-001', title: 'CSRF protection missing', description: 'State-changing endpoint without CSRF validation', severity: 'high', confidence: 'medium', category: 'Access Control', file_path: 'api/views.py', line_start: 45, code_snippet: '@csrf_exempt\ndef update_profile(request):', remediation: 'Remove @csrf_exempt and ensure CSRF token is sent', cwe_ids: ['CWE-352'], status: 'false_positive' },
    { rule_id: 'LEAK-002', title: 'Stack trace exposed', description: 'Debug mode enabled in production', severity: 'medium', confidence: 'high', category: 'Info Disclosure', file_path: 'config/settings.py', line_start: 10, code_snippet: 'DEBUG = True', remediation: 'Set DEBUG = False in production', cwe_ids: ['CWE-209'], status: 'open' },
    { rule_id: 'INJ-001', title: 'LDAP injection', description: 'User input in LDAP query', severity: 'high', confidence: 'medium', category: 'Injection', file_path: 'auth/ldap.py', line_start: 28, code_snippet: 'ldap.search(f"(uid={username})")', remediation: 'Escape LDAP special characters', cwe_ids: ['CWE-90'], status: 'open' },
    { rule_id: 'PATH-002', title: 'Zip Slip vulnerability', description: 'Path traversal in zip extraction', severity: 'high', confidence: 'high', category: 'Path Traversal', file_path: 'utils/archive.py', line_start: 15, code_snippet: 'zip.extractall("/app/uploads/")', remediation: 'Validate extracted file paths', cwe_ids: ['CWE-22'], status: 'open' },
    { rule_id: 'AUTH-004', title: 'JWT none algorithm', description: 'JWT accepts alg=none', severity: 'critical', confidence: 'high', category: 'Access Control', file_path: 'auth/jwt.py', line_start: 22, code_snippet: 'jwt.decode(token, verify=False)', remediation: 'Specify allowed algorithms explicitly', cwe_ids: ['CWE-287'], status: 'open' },
    { rule_id: 'SEC-001', title: 'Missing HSTS header', description: 'HTTP Strict Transport Security not enforced', severity: 'low', confidence: 'medium', category: 'Access Control', file_path: 'config/middleware.py', line_start: 18, code_snippet: '# HSTS middleware not configured', remediation: 'Add SecurityMiddleware with HSTS enabled', cwe_ids: ['CWE-319'], status: 'open' },
    { rule_id: 'DEP-002', title: 'Known vulnerable dependency', description: 'requests library has known CVE', severity: 'medium', confidence: 'high', category: 'Dependencies', file_path: 'requirements.txt', line_start: 5, code_snippet: 'requests==2.25.1', remediation: 'Upgrade to requests>=2.31.0', cwe_ids: ['CWE-1035'], status: 'open' },
  ];
  const all = [...FINDING_TEMPLATES, ...extra];
  return all.map((t, i) => ({
    ...t,
    id: `finding-${scanId}-${i}`,
    scan_id: scanId,
    project_id: projectId,
    created_at: new Date(Date.now() - Math.random() * 7 * 86400000).toISOString(),
  }));
}

const ALL_FINDINGS = ALL_SCANS.flatMap((s) => makeFindings(s.id, s.project_id));

// ---- QueryClient ----

import { QueryClient } from '@tanstack/react-query';
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30000, refetchOnWindowFocus: false },
  },
});

// ---- Hooks ----

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: async (): Promise<Project[]> => {
      await new Promise((r) => setTimeout(r, 300));
      return MOCK_PROJECTS;
    },
  });
}

export function useProject(id?: string) {
  return useQuery({
    queryKey: ['projects', id],
    queryFn: async (): Promise<Project | undefined> => {
      await new Promise((r) => setTimeout(r, 200));
      return MOCK_PROJECTS.find((p) => p.id === id);
    },
    enabled: !!id,
  });
}

export function useScans(projectId?: string) {
  return useQuery({
    queryKey: ['scans', projectId],
    queryFn: async (): Promise<Scan[]> => {
      await new Promise((r) => setTimeout(r, 300));
      if (projectId) return ALL_SCANS.filter((s) => s.project_id === projectId);
      return ALL_SCANS.slice(0, 10);
    },
  });
}

export function useScan(id?: string) {
  return useQuery({
    queryKey: ['scans', id],
    queryFn: async (): Promise<Scan | undefined> => {
      await new Promise((r) => setTimeout(r, 200));
      return ALL_SCANS.find((s) => s.id === id);
    },
    enabled: !!id,
  });
}

export function useFindings(scanId?: string, projectId?: string) {
  return useQuery({
    queryKey: ['findings', scanId, projectId],
    queryFn: async (): Promise<Finding[]> => {
      await new Promise((r) => setTimeout(r, 300));
      let results = [...ALL_FINDINGS];
      if (scanId) results = results.filter((f) => f.scan_id === scanId);
      if (projectId) results = results.filter((f) => f.project_id === projectId);
      return results;
    },
  });
}

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: async (): Promise<DashboardStats> => {
      await new Promise((r) => setTimeout(r, 300));
      const bySev: Record<Severity, number> = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
      ALL_FINDINGS.filter((f) => f.status === 'open').forEach((f) => {
        bySev[f.severity]++;
      });
      return {
        totalProjects: MOCK_PROJECTS.length,
        totalFindings: ALL_FINDINGS.filter((f) => f.status === 'open').length,
        avgRiskScore: Math.round((MOCK_PROJECTS.reduce((s, p) => s + p.risk_score, 0) / MOCK_PROJECTS.length) * 10) / 10,
        totalScans: ALL_SCANS.length,
        findingsBySeverity: bySev,
      };
    },
  });
}

export function useRiskTrend(projectId?: string) {
  return useQuery({
    queryKey: ['risk-trend', projectId],
    queryFn: async (): Promise<RiskTrendPoint[]> => {
      await new Promise((r) => setTimeout(r, 200));
      if (projectId) {
        return Array.from({ length: 15 }, (_, i) => ({
          date: new Date(Date.now() - (14 - i) * 86400000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
          risk_score: Math.round((5 + Math.sin(i * 0.4) * 2.5 + Math.random() * 1) * 10) / 10,
          scan_id: `scan-rt-${projectId}-${i}`,
          commit_sha: `abc${i}def${i}`.slice(0, 7),
        }));
      }
      return RISK_TREND;
    },
  });
}

export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (_: { projectId: string }) => {
      await new Promise((r) => setTimeout(r, 800));
      return { success: true };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scans'] });
      qc.invalidateQueries({ queryKey: ['stats'] });
    },
  });
}
