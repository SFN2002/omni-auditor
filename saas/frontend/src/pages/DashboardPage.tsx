import { motion } from 'framer-motion';
import { FolderGit, Activity, AlertCircle, ShieldAlert, TrendingUp, TrendingDown, Clock } from 'lucide-react';
import { Link } from 'react-router';
import { PieChart, Pie, Cell, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { useStats, useProjects, useScans } from '../hooks/useApi';
import MetricCard from '../components/MetricCard';
import RiskScore from '../components/RiskScore';
import StatusBadge from '../components/StatusBadge';
import BaselineBadge from '../components/BaselineBadge';
import type { Severity } from '../types';

const SEVERITY_COLORS: Record<Severity, string> = {
  critical: '#EF4444',
  high: '#F97316',
  medium: '#EAB308',
  low: '#3B82F6',
  info: '#6B7280',
};

export default function DashboardPage() {
  const { data: stats } = useStats();
  const { data: projects } = useProjects();
  const { data: scans } = useScans();

  const pieData = stats
    ? (Object.entries(stats.findingsBySeverity) as [Severity, number][])
        .filter(([, v]) => v > 0)
        .map(([k, v]) => ({ name: k.charAt(0).toUpperCase() + k.slice(1), value: v, color: SEVERITY_COLORS[k] }))
    : [];

  const recentScans = scans?.slice(0, 5) ?? [];

  const riskTrend = Array.from({ length: 15 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (14 - i));
    return {
      date: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      score: Math.round((4 + Math.sin(i * 0.4) * 2.5 + Math.random() * 1.5) * 10) / 10,
    };
  });

  return (
    <div>
      {/* Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        <MetricCard icon={FolderGit} iconColor="text-accent-primary" value={stats?.totalProjects ?? 0} label="TOTAL PROJECTS" secondary="+2 this month" secondaryColor="text-green-400" trend="up" delay={0} />
        <MetricCard icon={AlertCircle} iconColor="text-sev-high" value={stats?.totalFindings ?? 0} label="OPEN FINDINGS" secondary="↓ 12% from last week" secondaryColor="text-green-400" trend="down" delay={1} />
        <MetricCard icon={ShieldAlert} iconColor="text-sev-medium" value={stats?.avgRiskScore ?? 0} label="AVG RISK SCORE" secondary="Low Risk" secondaryColor="text-blue-400" delay={2}>
          <RiskScore score={stats?.avgRiskScore ?? 0} size="sm" />
        </MetricCard>
        <MetricCard icon={Activity} iconColor="text-accent-secondary" value={stats?.totalScans ?? 0} label="TOTAL SCANS" secondary="Last scan 2m ago" secondaryColor="text-text-tertiary" delay={3} />
      </div>

      {/* Charts row */}
      <div className="grid lg:grid-cols-5 gap-6 mt-6">
        {/* Severity Distribution */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="lg:col-span-3 card rounded-lg p-6"
        >
          <h3 className="text-base font-semibold text-text-primary mb-4">Severity Distribution</h3>
          <div className="flex flex-col sm:flex-row items-center gap-8">
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie
                  data={pieData} cx="50%" cy="50%"
                  innerRadius={60} outerRadius={90}
                  paddingAngle={2} dataKey="value"
                  stroke="none"
                >
                  {pieData.map((entry, idx) => (
                    <Cell key={idx} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#0F172A', border: '1px solid #334155', borderRadius: '8px', color: '#F8FAFC' }}
                  formatter={(value: number, name: string) => [`${value} findings`, name]}
                />
              </PieChart>
            </ResponsiveContainer>
            <div className="flex flex-col gap-3 min-w-[140px]">
              {pieData.map((s) => (
                <div key={s.name} className="flex items-center gap-2">
                  <div className="w-3 h-3 rounded-full" style={{ background: s.color }} />
                  <span className="text-sm text-text-secondary">{s.name}: <span className="text-text-primary font-medium">{s.value}</span></span>
                </div>
              ))}
              <div className="border-t border-border-default pt-2 mt-1">
                <span className="text-sm text-text-tertiary">Total: <span className="text-text-primary font-bold">{pieData.reduce((a, b) => a + b.value, 0)}</span></span>
              </div>
            </div>
          </div>
        </motion.div>

        {/* Risk Trend */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="lg:col-span-2 card rounded-lg p-6"
        >
          <h3 className="text-base font-semibold text-text-primary mb-4">Risk Trend (14 Days)</h3>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={riskTrend}>
              <defs>
                <linearGradient id="riskGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3B82F6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" opacity={0.3} />
              <XAxis dataKey="date" tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 10]} tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#0F172A', border: '1px solid #334155', borderRadius: '8px', color: '#F8FAFC' }}
                formatter={(v: number) => [`Risk: ${v}`, '']}
              />
              <Area type="monotone" dataKey="score" stroke="#3B82F6" strokeWidth={2} fill="url(#riskGrad)" />
            </AreaChart>
          </ResponsiveContainer>
        </motion.div>
      </div>

      {/* Projects Table */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
        className="mt-6 card rounded-lg p-6"
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-text-primary">All Projects</h3>
          <Link to="/projects" className="text-sm text-accent-primary hover:underline">View All &rarr;</Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border-default">
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Project</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Risk Score</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Findings</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Last Scan</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Baseline</th>
              </tr>
            </thead>
            <tbody>
              {projects?.map((p, i) => (
                <motion.tr
                  key={p.id}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: i * 0.05 }}
                  className="border-b border-border-default last:border-0 hover:bg-bg-surface-hover transition-colors"
                >
                  <td className="py-3 px-4">
                    <Link to={`/projects/${p.id}`} className="text-sm font-medium text-text-primary hover:text-accent-primary">{p.name}</Link>
                    {p.description && <div className="text-xs text-text-tertiary truncate max-w-[200px]">{p.description}</div>}
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-semibold" style={{ color: p.risk_score > 7 ? '#EF4444' : p.risk_score > 4 ? '#EAB308' : '#3B82F6' }}>{p.risk_score.toFixed(1)}</span>
                      <div className="w-16 h-1.5 rounded-full bg-bg-elevated overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${p.risk_score * 10}%`, background: p.risk_score > 7 ? '#EF4444' : p.risk_score > 4 ? '#EAB308' : '#3B82F6' }}
                        />
                      </div>
                    </div>
                  </td>
                  <td className="py-3 px-4 text-sm text-text-secondary">{p.findings_count}</td>
                  <td className="py-3 px-4 text-sm text-text-secondary">
                    {p.last_scan_at ? <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(p.last_scan_at).toLocaleDateString()}</span> : 'Never'}
                  </td>
                  <td className="py-3 px-4"><BaselineBadge status={p.baseline_status} /></td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      </motion.div>

      {/* Recent Scans */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.6 }}
        className="mt-6 card rounded-lg p-6"
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-text-primary">Recent Scans</h3>
          <Link to="/scans" className="text-sm text-accent-primary hover:underline">View All &rarr;</Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border-default">
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Project</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Status</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Findings</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Risk</th>
                <th className="py-3 px-4 text-xs font-semibold uppercase tracking-wider text-text-secondary">Date</th>
              </tr>
            </thead>
            <tbody>
              {recentScans.map((s, i) => (
                <motion.tr
                  key={s.id}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: i * 0.03 }}
                  className="border-b border-border-default last:border-0 hover:bg-bg-surface-hover transition-colors"
                >
                  <td className="py-3 px-4">
                    <Link to={`/projects/${s.project_id}`} className="text-sm text-accent-primary hover:underline">
                      {projects?.find((p) => p.id === s.project_id)?.name ?? s.project_id}
                    </Link>
                  </td>
                  <td className="py-3 px-4"><StatusBadge status={s.status} /></td>
                  <td className="py-3 px-4 text-sm text-text-secondary">{s.findings_count || '—'}</td>
                  <td className="py-3 px-4">
                    {s.risk_score != null ? (
                      <span className="text-sm font-medium" style={{ color: s.risk_score > 7 ? '#EF4444' : s.risk_score > 4 ? '#EAB308' : '#3B82F6' }}>{s.risk_score.toFixed(1)}</span>
                    ) : '—'}
                  </td>
                  <td className="py-3 px-4 text-sm text-text-secondary">
                    {s.completed_at ? new Date(s.completed_at).toLocaleDateString() : s.started_at ? new Date(s.started_at).toLocaleDateString() : '—'}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      </motion.div>
    </div>
  );
}
