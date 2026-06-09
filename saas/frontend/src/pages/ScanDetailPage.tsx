import { useParams, Link } from 'react-router';
import { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Activity, Copy, CheckCircle, Download, RefreshCw, ChevronDown, ChevronUp, Search, GitBranch, Clock, FileCode, AlertCircle, ShieldAlert } from 'lucide-react';
import { RadarChart, PolarGrid, PolarAngleAxis, Radar, ResponsiveContainer, Tooltip as RechartsTooltip } from 'recharts';
import { useScan, useFindings, useProject } from '../hooks/useApi';
import MetricCard from '../components/MetricCard';
import RiskScore from '../components/RiskScore';
import StatusBadge from '../components/StatusBadge';
import BaselineBadge from '../components/BaselineBadge';
import SeverityBadge from '../components/SeverityBadge';
import FilterPills from '../components/FilterPills';
import type { Severity, Finding } from '../types';

const RADAR_COLORS: Record<string, string> = {
  'SQL Injection': '#EF4444',
  'XSS': '#F97316',
  'Command Injection': '#EAB308',
  'Secrets Mgmt': '#3B82F6',
  'Access Control': '#8B5CF6',
  'Crypto Failures': '#06B6D4',
};

export default function ScanDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: scan } = useScan(id);
  const { data: findings } = useFindings(id);
  const { data: project } = useProject(scan?.project_id);
  const [severityFilter, setSeverityFilter] = useState<Severity | 'all'>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [expandedFinding, setExpandedFinding] = useState<string | null>(null);
  const [copiedSha, setCopiedSha] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const perPage = 10;

  const filteredFindings = useMemo(() => {
    if (!findings) return [];
    let results = [...findings];
    if (severityFilter !== 'all') {
      results = results.filter((f) => f.severity === severityFilter);
    }
    if (searchTerm) {
      const q = searchTerm.toLowerCase();
      results = results.filter((f) =>
        f.title.toLowerCase().includes(q) ||
        f.rule_id.toLowerCase().includes(q) ||
        f.file_path?.toLowerCase().includes(q) ||
        f.category?.toLowerCase().includes(q)
      );
    }
    return results;
  }, [findings, severityFilter, searchTerm]);

  const severityCounts = useMemo(() => {
    const counts: Record<Severity | 'all', number> = { all: findings?.length ?? 0, critical: 0, high: 0, medium: 0, low: 0, info: 0 };
    findings?.forEach((f) => { if (counts[f.severity] !== undefined) counts[f.severity]++; });
    return counts;
  }, [findings]);

  const paginated = filteredFindings.slice((currentPage - 1) * perPage, currentPage * perPage);
  const totalPages = Math.ceil(filteredFindings.length / perPage);

  const radarData = useMemo(() => {
    if (!scan?.risk_vector_90d) return [];
    return Object.entries(scan.risk_vector_90d).map(([axis, value]) => ({
      axis,
      value,
      fullMark: 10,
    }));
  }, [scan?.risk_vector_90d]);

  const overallVectorScore = useMemo(() => {
    if (!radarData.length) return 0;
    return Math.round((radarData.reduce((s, d) => s + d.value, 0) / radarData.length) * 10) / 10;
  }, [radarData]);

  const copySha = () => {
    if (scan?.commit_sha) {
      navigator.clipboard.writeText(scan.commit_sha);
      setCopiedSha(true);
      setTimeout(() => setCopiedSha(false), 2000);
    }
  };

  const exportSarif = () => {
    const sarif = {
      version: '2.1.0',
      $schema: 'https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json',
      runs: [{
        tool: { driver: { name: 'Omni-Auditor', informationUri: 'https://omni-auditor.io', rules: findings?.map((f) => ({
          id: f.rule_id, name: f.title, shortDescription: { text: f.description || f.title }, defaultConfiguration: { level: f.severity === 'critical' ? 'error' : f.severity === 'high' ? 'error' : f.severity === 'medium' ? 'warning' : 'note' },
        })) ?? [] } },
        results: filteredFindings.map((f) => ({
          ruleId: f.rule_id, message: { text: f.description || f.title }, level: f.severity === 'critical' ? 'error' : f.severity === 'high' ? 'error' : f.severity === 'medium' ? 'warning' : 'note',
          locations: f.file_path ? [{ physicalLocation: { artifactLocation: { uri: f.file_path }, region: { startLine: f.line_start ?? 1 } } }] : [],
          properties: { severity: f.severity, category: f.category, remediation: f.remediation },
        })),
      }],
    };
    const blob = new Blob([JSON.stringify(sarif, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `omni-auditor-scan-${id?.slice(-4)}.sarif`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const toggleExpand = (findingId: string) => {
    setExpandedFinding((prev) => (prev === findingId ? null : findingId));
  };

  if (!scan) {
    return <div className="flex items-center justify-center h-64 text-text-secondary">Loading scan details...</div>;
  }

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mb-4 text-sm text-text-secondary">
        <Link to="/dashboard" className="hover:text-text-primary">Dashboard</Link>
        <span>/</span>
        <Link to="/projects" className="hover:text-text-primary">Projects</Link>
        <span>/</span>
        {project && <Link to={`/projects/${project.id}`} className="hover:text-text-primary">{project.name}</Link>}
        {project && <span>/</span>}
        <span className="text-text-primary font-medium">Scan #{id?.slice(-3)}</span>
      </div>

      {/* Scan Header */}
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="card rounded-lg p-6 mb-6">
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-accent-primary" />
              <h2 className="text-xl font-bold text-text-primary">Scan #{id?.slice(-3)}</h2>
            </div>
            {project && (
              <Link to={`/projects/${project.id}`} className="text-sm text-accent-primary hover:underline mt-1 inline-block">{project.name}</Link>
            )}
            <div className="text-xs text-text-secondary mt-1">
              Triggered by {scan.triggered_by} &bull; {scan.branch}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge status={scan.status} />
            {scan.started_at && (
              <span className="flex items-center gap-1 text-sm text-text-secondary">
                <Clock className="w-4 h-4" />
                {scan.completed_at
                  ? `${Math.round((new Date(scan.completed_at).getTime() - new Date(scan.started_at).getTime()) / 60000)}m`
                  : 'In progress'}
              </span>
            )}
          </div>
        </div>

        {/* Commit info */}
        {scan.commit_sha && (
          <div className="mt-4 bg-bg-elevated rounded-md px-4 py-3 flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-text-tertiary" />
              <span className="text-xs font-mono text-text-tertiary">{scan.commit_sha}</span>
              <button onClick={copySha} className="p-1 rounded hover:bg-bg-surface text-text-tertiary hover:text-text-primary transition-colors">
                {copiedSha ? <CheckCircle className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            <span className="px-2 py-0.5 bg-accent-primary/10 text-accent-primary text-xs font-medium rounded-sm">{scan.branch}</span>
            <span className="text-xs text-text-tertiary ml-auto">
              {new Date(scan.created_at).toLocaleString()}
            </span>
          </div>
        )}

        {/* Actions */}
        <div className="mt-4 flex items-center gap-3">
          <button onClick={exportSarif} className="flex items-center gap-2 px-4 py-2 border border-border-default text-text-primary text-sm rounded-md hover:bg-bg-surface transition-all">
            <Download className="w-4 h-4" /> Export SARIF
          </button>
          <button className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white text-sm font-semibold rounded-md hover:bg-accent-primary-hover transition-all">
            <RefreshCw className="w-4 h-4" /> Re-run Scan
          </button>
        </div>
      </motion.div>

      {/* Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-6">
        <MetricCard icon={FileCode} iconColor="text-accent-primary" value="1,247" label="FILES SCANNED" secondary="+23 from last scan" secondaryColor="text-green-400" delay={0} />
        <MetricCard icon={AlertCircle} iconColor="text-sev-high" value={scan.findings_count} label="TOTAL FINDINGS" secondary="↓ 4 from last scan" secondaryColor="text-green-400" delay={1} />
        <MetricCard icon={ShieldAlert} iconColor="text-sev-high" value={scan.risk_score?.toFixed(1) ?? '—'} label="RISK SCORE" secondary={scan.risk_score && scan.risk_score > 6 ? 'High Risk' : 'Medium Risk'} secondaryColor={scan.risk_score && scan.risk_score > 6 ? 'text-orange-400' : 'text-yellow-400'} delay={2}>
          <RiskScore score={scan.risk_score ?? 0} size="sm" />
        </MetricCard>
        <MetricCard icon={Clock} iconColor="text-accent-primary" value={scan.started_at && scan.completed_at ? `${Math.round((new Date(scan.completed_at).getTime() - new Date(scan.started_at).getTime()) / 60000)}m ${Math.round(((new Date(scan.completed_at).getTime() - new Date(scan.started_at).getTime()) % 60000) / 1000)}s` : '—'} label="SCAN DURATION" secondary="Avg: 2m 12s" secondaryColor="text-text-tertiary" delay={3} />
      </div>

      {/* Main Content: Findings + Risk Vector */}
      <div className="grid lg:grid-cols-5 gap-6">
        {/* Findings Table */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="lg:col-span-3 card rounded-lg p-6"
        >
          <div className="flex flex-col gap-4 mb-4">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold text-text-primary">Findings ({filteredFindings.length})</h3>
              <div className="flex items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-tertiary" />
                  <input
                    type="text"
                    placeholder="Search findings..."
                    value={searchTerm}
                    onChange={(e) => { setSearchTerm(e.target.value); setCurrentPage(1); }}
                    className="pl-9 pr-3 py-1.5 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent-primary w-44"
                  />
                </div>
              </div>
            </div>
            <FilterPills active={severityFilter} onChange={(s) => { setSeverityFilter(s); setCurrentPage(1); }} counts={severityCounts} />
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border-default">
                  <th className="py-2 px-3 text-xs font-semibold uppercase tracking-wider text-text-secondary w-8"></th>
                  <th className="py-2 px-3 text-xs font-semibold uppercase tracking-wider text-text-secondary">Severity</th>
                  <th className="py-2 px-3 text-xs font-semibold uppercase tracking-wider text-text-secondary">Rule</th>
                  <th className="py-2 px-3 text-xs font-semibold uppercase tracking-wider text-text-secondary">Location</th>
                  <th className="py-2 px-3 text-xs font-semibold uppercase tracking-wider text-text-secondary">Category</th>
                  <th className="py-2 px-3 text-xs font-semibold uppercase tracking-wider text-text-secondary">Status</th>
                </tr>
              </thead>
              <tbody>
                {paginated.length === 0 && (
                  <tr><td colSpan={6} className="py-12 text-center text-text-tertiary">No findings match your filters</td></tr>
                )}
                {paginated.map((f, i) => (
                  <>
                    <motion.tr
                      key={f.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: i * 0.025 }}
                      onClick={() => toggleExpand(f.id)}
                      className={`border-b border-border-default cursor-pointer transition-colors ${expandedFinding === f.id ? 'bg-bg-surface-hover' : 'hover:bg-bg-surface-hover'}`}
                    >
                      <td className="py-2 px-3">
                        {expandedFinding === f.id ? <ChevronUp className="w-4 h-4 text-text-tertiary" /> : <ChevronDown className="w-4 h-4 text-text-tertiary" />}
                      </td>
                      <td className="py-2 px-3"><SeverityBadge severity={f.severity} /></td>
                      <td className="py-2 px-3">
                        <div className="font-mono text-xs text-accent-primary">{f.rule_id}</div>
                        <div className="text-xs text-text-secondary truncate max-w-[160px]">{f.title}</div>
                      </td>
                      <td className="py-2 px-3">
                        <div className="font-mono text-xs text-text-secondary">{f.file_path}</div>
                        {f.line_start && <div className="text-xs text-text-tertiary">line {f.line_start}{f.line_end ? `-${f.line_end}` : ''}</div>}
                      </td>
                      <td className="py-2 px-3 text-xs text-text-secondary">{f.category ?? '—'}</td>
                      <td className="py-2 px-3">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-sm text-xs font-medium border ${f.status === 'open' ? 'bg-red-500/15 text-red-400 border-red-500/30' : f.status === 'fixed' ? 'bg-green-500/15 text-green-400 border-green-500/30' : 'bg-gray-500/15 text-gray-400 border-gray-500/30'}`}>
                          {f.status === 'false_positive' ? 'False +' : f.status === 'accepted' ? 'Ignored' : f.status}
                        </span>
                      </td>
                    </motion.tr>
                    <AnimatePresence>
                      {expandedFinding === f.id && (
                        <motion.tr
                          key={`${f.id}-detail`}
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.3 }}
                        >
                          <td colSpan={6} className="px-4 py-3 bg-bg-elevated/50">
                            <div className="space-y-3">
                              {f.description && (
                                <div>
                                  <div className="text-xs uppercase tracking-wider text-text-tertiary mb-1">Description</div>
                                  <p className="text-sm text-text-secondary">{f.description}</p>
                                </div>
                              )}
                              {f.code_snippet && (
                                <div>
                                  <div className="text-xs uppercase tracking-wider text-text-tertiary mb-1">Code Snippet</div>
                                  <pre className="bg-[#0d1117] border border-border-default rounded-md p-3 overflow-x-auto">
                                    <code className="text-xs font-mono text-text-primary">{f.code_snippet}</code>
                                  </pre>
                                </div>
                              )}
                              {f.remediation && (
                                <div>
                                  <div className="text-xs uppercase tracking-wider text-text-tertiary mb-1">Remediation</div>
                                  <p className="text-sm text-green-400">{f.remediation}</p>
                                </div>
                              )}
                              {f.cwe_ids && f.cwe_ids.length > 0 && (
                                <div className="flex items-center gap-2">
                                  <span className="text-xs text-text-tertiary">CWEs:</span>
                                  {f.cwe_ids.map((cwe) => (
                                    <span key={cwe} className="px-2 py-0.5 bg-accent-primary/10 text-accent-primary text-xs rounded-sm">{cwe}</span>
                                  ))}
                                </div>
                              )}
                              <div className="flex items-center gap-2 pt-2">
                                <button className="px-3 py-1.5 bg-green-500/10 text-green-400 text-xs font-medium rounded-md border border-green-500/30 hover:bg-green-500/20 transition-colors">Mark as Fixed</button>
                                <button className="px-3 py-1.5 bg-gray-500/10 text-gray-400 text-xs font-medium rounded-md border border-gray-500/30 hover:bg-gray-500/20 transition-colors">False Positive</button>
                                <button className="px-3 py-1.5 bg-gray-500/10 text-gray-400 text-xs font-medium rounded-md border border-gray-500/30 hover:bg-gray-500/20 transition-colors">Ignore</button>
                              </div>
                            </div>
                          </td>
                        </motion.tr>
                      )}
                    </AnimatePresence>
                  </>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-border-default">
              <span className="text-xs text-text-tertiary">{filteredFindings.length} findings</span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  disabled={currentPage === 1}
                  className="px-3 py-1.5 text-xs rounded-md border border-border-default text-text-secondary hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  Prev
                </button>
                <span className="px-3 py-1.5 text-xs text-text-primary">{currentPage} / {totalPages}</span>
                <button
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  disabled={currentPage === totalPages}
                  className="px-3 py-1.5 text-xs rounded-md border border-border-default text-text-secondary hover:bg-bg-surface disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </motion.div>

        {/* Right Column: Radar Chart + Severity Bars */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="lg:col-span-2 space-y-6"
        >
          {/* 90-D Risk Vector */}
          <div className="card rounded-lg p-6">
            <h3 className="text-base font-semibold text-text-primary mb-1">90-D Risk Vector</h3>
            <p className="text-xs text-text-tertiary mb-4">Vulnerability distribution across attack categories</p>
            <ResponsiveContainer width="100%" height={300}>
              <RadarChart cx="50%" cy="50%" outerRadius="70%" data={radarData}>
                <PolarGrid stroke="#334155" strokeOpacity={0.4} />
                <PolarAngleAxis dataKey="axis" tick={{ fill: '#94A3B8', fontSize: 11 }} />
                <RechartsTooltip contentStyle={{ background: '#0F172A', border: '1px solid #334155', borderRadius: '8px', color: '#F8FAFC' }} />
                <Radar
                  name="Risk"
                  dataKey="value"
                  stroke="#3B82F6"
                  fill="#3B82F6"
                  fillOpacity={0.15}
                  strokeWidth={2}
                />
              </RadarChart>
            </ResponsiveContainer>

            {/* Severity bars below radar */}
            <div className="mt-4 space-y-3">
              {radarData.map((d) => (
                <div key={d.axis} className="flex items-center gap-3">
                  <span className="text-xs text-text-secondary w-28 truncate text-right">{d.axis}</span>
                  <div className="flex-1 h-2 bg-bg-elevated rounded-full overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${(d.value / 10) * 100}%` }}
                      transition={{ duration: 0.6, delay: 0.2 }}
                      className="h-full rounded-full"
                      style={{ background: RADAR_COLORS[d.axis] || '#3B82F6' }}
                    />
                  </div>
                  <span className="text-xs font-mono text-text-primary w-8">{d.value}</span>
                </div>
              ))}
            </div>

            {/* Overall Score */}
            <div className="mt-4 pt-4 border-t border-border-default flex items-center justify-between">
              <span className="text-sm text-text-secondary">Overall Vector Score</span>
              <span className={`text-lg font-bold ${overallVectorScore > 7 ? 'text-red-400' : overallVectorScore > 4 ? 'text-yellow-400' : 'text-blue-400'}`}>
                {overallVectorScore}/10
              </span>
            </div>
          </div>

          {/* Baseline Status */}
          <div className="card rounded-lg p-6">
            <h3 className="text-base font-semibold text-text-primary mb-3">Baseline Comparison</h3>
            <div className="flex items-center gap-3">
              <BaselineBadge status={scan.baseline_status as 'IMPROVED' | 'STABLE' | 'DEGRADED' | 'FRACTURED'} />
              <span className="text-sm text-text-secondary">
                {scan.baseline_status === 'IMPROVED' ? 'Risk score trending downward. Good job!' :
                 scan.baseline_status === 'DEGRADED' ? 'Risk score has increased since last scan.' :
                 'No significant change from previous scan.'}
              </span>
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
