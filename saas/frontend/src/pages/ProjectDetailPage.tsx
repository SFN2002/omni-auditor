import { useParams, Link } from 'react-router';
import { motion } from 'framer-motion';
import { ArrowLeft, GitBranch, ExternalLink, Play, ShieldAlert, AlertTriangle, Activity, Clock, TrendingDown, TrendingUp } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { useProject, useScans, useFindings, useRiskTrend, useTriggerScan } from '../hooks/useApi';
import MetricCard from '../components/MetricCard';
import RiskScore from '../components/RiskScore';
import BaselineBadge from '../components/BaselineBadge';
import StatusBadge from '../components/StatusBadge';
import { useState } from 'react';

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: project } = useProject(id);
  const { data: scans } = useScans(id);
  const { data: findings } = useFindings(undefined, id);
  const { data: trend } = useRiskTrend(id);
  const [trendRange, setTrendRange] = useState<7 | 30 | 90>(30);
  const triggerScan = useTriggerScan();

  const openFindings = findings?.filter((f) => f.status === 'open').length ?? 0;
  const avgResTime = '2.4d';

  const filteredTrend = trend?.slice(-trendRange) ?? [];

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mb-4 text-sm text-text-secondary">
        <Link to="/dashboard" className="hover:text-text-primary">Dashboard</Link>
        <span>/</span>
        <Link to="/projects" className="hover:text-text-primary">Projects</Link>
        <span>/</span>
        <span className="text-text-primary font-medium">{project?.name ?? id}</span>
      </div>

      {/* Project Header */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="card rounded-lg p-6 mb-6"
      >
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <Link to="/projects" className="p-1.5 rounded-md hover:bg-bg-surface-hover transition-colors">
                <ArrowLeft className="w-5 h-5 text-text-secondary" />
              </Link>
              <GitBranch className="w-5 h-5 text-text-secondary" />
              <h2 className="text-2xl font-bold text-text-primary">{project?.name ?? id}</h2>
            </div>
            {project?.github_repo && (
              <a href={`https://github.com/${project.github_repo}`} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 mt-1 ml-12 text-xs text-text-tertiary hover:text-accent-primary">
                github.com/{project.github_repo} <ExternalLink className="w-3 h-3" />
              </a>
            )}
            <div className="flex items-center gap-2 mt-2 ml-12">
              <span className="px-2 py-0.5 bg-bg-elevated border border-border-default rounded-sm text-xs text-text-secondary">Python 3.11</span>
              <span className="px-2 py-0.5 bg-bg-elevated border border-border-default rounded-sm text-xs text-text-secondary">FastAPI</span>
              <span className="px-2 py-0.5 bg-green-500/10 border border-green-500/30 rounded-sm text-xs text-green-400">Active</span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => id && triggerScan.mutate({ projectId: id })}
              className="flex items-center gap-2 px-4 py-2 bg-accent-primary text-white text-sm font-semibold rounded-md hover:bg-accent-primary-hover transition-all"
            >
              <Play className="w-4 h-4" /> Run New Scan
            </button>
            {scans && scans[0] && (
              <Link to={`/scans/${scans[0].id}`} className="flex items-center gap-2 px-4 py-2 border border-border-default text-text-primary text-sm rounded-md hover:bg-bg-surface transition-all">
                <ExternalLink className="w-4 h-4" /> View Latest Scan
              </Link>
            )}
          </div>
        </div>
      </motion.div>

      {/* Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-6">
        <MetricCard icon={ShieldAlert} iconColor="text-sev-high" value={project?.risk_score?.toFixed(1) ?? '—'} label="CURRENT RISK SCORE" secondary={project && project.risk_score > 6 ? 'High Risk' : project && project.risk_score > 4 ? 'Medium Risk' : 'Low Risk'} secondaryColor={project && project.risk_score > 6 ? 'text-orange-400' : project && project.risk_score > 4 ? 'text-yellow-400' : 'text-green-400'} delay={0}>
          <RiskScore score={project?.risk_score ?? 0} size="sm" />
        </MetricCard>
        <MetricCard icon={AlertTriangle} iconColor="text-sev-high" value={openFindings} label="OPEN FINDINGS" secondary="↓ 4 from last scan" secondaryColor="text-green-400" delay={1} />
        <MetricCard icon={Activity} iconColor="text-accent-secondary" value={project?.scans_count ?? 0} label="TOTAL SCANS" secondary="+3 this week" secondaryColor="text-green-400" delay={2} />
        <MetricCard icon={Clock} iconColor="text-accent-primary" value={avgResTime} label="AVG RESOLUTION TIME" secondary="Per finding" secondaryColor="text-text-tertiary" delay={3} />
      </div>

      {/* Risk Trend + Scan History */}
      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="lg:col-span-2 card rounded-lg p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-semibold text-text-primary">Risk Trend ({trendRange} Days)</h3>
            <div className="flex items-center gap-1 bg-bg-elevated rounded-md p-0.5">
              {([7, 30, 90] as const).map((r) => (
                <button
                  key={r}
                  onClick={() => setTrendRange(r)}
                  className={`px-3 py-1 text-xs font-medium rounded-sm transition-all ${trendRange === r ? 'bg-accent-primary text-white' : 'text-text-secondary hover:text-text-primary'}`}
                >
                  {r}D
                </button>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={filteredTrend}>
              <defs>
                <linearGradient id="trendGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="#3B82F6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 10]} tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ background: '#0F172A', border: '1px solid #334155', borderRadius: '8px', color: '#F8FAFC' }} />
              <ReferenceLine y={7} stroke="#F97316" strokeDasharray="4 4" label={{ value: 'High Threshold', fill: '#F97316', fontSize: 11, position: 'right' }} />
              <Area type="monotone" dataKey="risk_score" stroke="#3B82F6" strokeWidth={2.5} fill="url(#trendGrad)" dot={{ r: 3, fill: '#3B82F6', stroke: '#fff', strokeWidth: 2 }} />
            </AreaChart>
          </ResponsiveContainer>
        </motion.div>

        {/* Scan History */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="card rounded-lg p-6"
        >
          <h3 className="text-base font-semibold text-text-primary mb-4">Scan History</h3>
          <div className="relative">
            {/* Timeline rail */}
            <div className="absolute left-[19px] top-2 bottom-2 w-0.5 bg-border-default rounded-full" />
            <div className="space-y-4 max-h-[280px] overflow-y-auto pr-1">
              {scans?.slice(0, 8).map((s, i) => (
                <motion.div
                  key={s.id}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.08, duration: 0.4 }}
                >
                  <Link to={`/scans/${s.id}`} className="flex items-start gap-3 group hover:bg-bg-surface-hover -mx-2 px-2 py-1.5 rounded-md transition-all">
                    <div className={`w-2.5 h-2.5 rounded-full mt-1.5 flex-shrink-0 ${s.status === 'completed' ? 'bg-green-500' : s.status === 'failed' ? 'bg-red-500' : s.status === 'running' ? 'bg-blue-500 animate-pulse' : 'bg-gray-500'}`} />
                    <div className="min-w-0">
                      <div className="text-sm text-text-primary group-hover:text-accent-primary transition-colors">
                        {s.triggered_by === 'ci-cd' ? 'CI/CD Trigger' : `Manual by ${s.triggered_by}`}
                      </div>
                      <div className="text-xs text-text-tertiary mt-0.5">
                        {s.branch} &bull; {s.risk_score != null ? `Score: ${s.risk_score.toFixed(1)}` : s.status}
                      </div>
                      {s.findings_count > 0 && (
                        <div className="text-xs text-text-secondary mt-0.5">{s.findings_count} findings</div>
                      )}
                    </div>
                  </Link>
                </motion.div>
              ))}
              {(!scans || scans.length === 0) && (
                <p className="text-sm text-text-tertiary pl-8">No scans yet</p>
              )}
            </div>
          </div>
        </motion.div>
      </div>

      {/* Recent Activity */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="card rounded-lg p-6"
      >
        <h3 className="text-base font-semibold text-text-primary mb-4">Recent Activity</h3>
        <div className="space-y-0">
          {[
            { user: 'Sarah Chen', initials: 'SC', action: 'closed finding', target: 'SQL Injection in payments/views.py:142', severity: 'critical', time: '5m ago' },
            { user: 'Mike Torres', initials: 'MT', action: 'commented on', target: 'Hardcoded API Key in config/settings.py:23', severity: null, time: '1h ago', comment: 'Need to rotate this key ASAP' },
            { user: 'Alex Kim', initials: 'AK', action: 'new finding', target: 'Path Traversal in uploads/handlers.py:89', severity: 'medium', time: '2h ago' },
            { user: 'Sarah Chen', initials: 'SC', action: 'scan completed', target: '12 findings, risk score 6.8', severity: null, time: '2h ago' },
          ].map((item, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.04 }}
              className="flex items-start gap-3 py-3 border-b border-border-default last:border-0"
            >
              <div className="w-8 h-8 rounded-full bg-accent-primary/20 flex items-center justify-center text-accent-primary text-xs font-semibold flex-shrink-0">
                {item.initials}
              </div>
              <div className="min-w-0">
                <div className="text-sm text-text-primary">
                  <span className="font-medium">{item.user}</span>{' '}
                  <span className="text-text-secondary">{item.action}</span>{' '}
                  <span className="font-medium text-accent-primary">{item.target}</span>
                </div>
                {item.comment && <div className="text-xs text-text-tertiary mt-1">&quot;{item.comment}&quot;</div>}
                <div className="text-xs text-text-tertiary mt-1">{item.time}</div>
              </div>
            </motion.div>
          ))}
        </div>
      </motion.div>
    </div>
  );
}
