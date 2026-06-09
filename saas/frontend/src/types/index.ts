export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type FindingStatus = 'open' | 'fixed' | 'false_positive' | 'accepted';
export type BaselineStatus = 'IMPROVED' | 'STABLE' | 'DEGRADED' | 'FRACTURED';
export type ScanStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface User {
  id: string;
  username: string;
  email: string;
  name: string;
  avatar_url?: string;
  role: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  member_count: number;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description?: string;
  github_repo?: string;
  risk_score: number;
  last_scan_at?: string;
  findings_count: number;
  scans_count: number;
  baseline_status?: BaselineStatus;
}

export interface Scan {
  id: string;
  project_id: string;
  status: ScanStatus;
  commit_sha?: string;
  branch: string;
  risk_score?: number;
  risk_vector_90d?: Record<string, number>;
  findings_count: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  info_count: number;
  baseline_status?: string;
  triggered_by: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
}

export interface Finding {
  id: string;
  scan_id: string;
  project_id: string;
  rule_id: string;
  title: string;
  description?: string;
  severity: Severity;
  confidence?: string;
  category?: string;
  file_path?: string;
  line_start?: number;
  line_end?: number;
  code_snippet?: string;
  remediation?: string;
  cwe_ids?: string[];
  status: FindingStatus;
  created_at: string;
}

export interface DashboardStats {
  totalProjects: number;
  totalFindings: number;
  avgRiskScore: number;
  totalScans: number;
  findingsBySeverity: Record<Severity, number>;
}

export interface RiskTrendPoint {
  date: string;
  risk_score: number;
  scan_id: string;
  commit_sha?: string;
}
