import { useState } from 'react';
import { motion } from 'framer-motion';
import { Search, Clock, GitBranch, ArrowRight } from 'lucide-react';
import { Link } from 'react-router';
import { useScans, useProjects } from '../hooks/useApi';
import StatusBadge from '../components/StatusBadge';
import DataTable from '../components/DataTable';
import type { Scan } from '../types';

export default function ScansPage() {
  const { data: scans } = useScans();
  const { data: projects } = useProjects();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const filtered = scans?.filter((s) => {
    const projectName = projects?.find((p) => p.id === s.project_id)?.name ?? '';
    const matchesSearch = !search || projectName.toLowerCase().includes(search.toLowerCase()) || s.branch.toLowerCase().includes(search.toLowerCase());
    const matchesStatus = statusFilter === 'all' || s.status === statusFilter;
    return matchesSearch && matchesStatus;
  }) ?? [];

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h2 className="text-xl font-semibold text-text-primary">Scans</h2>
          <p className="text-sm text-text-secondary mt-1">{filtered.length} scan{filtered.length !== 1 ? 's' : ''}</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-tertiary" />
            <input
              type="text"
              placeholder="Search scans..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10 pr-4 py-2 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent-primary w-48"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary outline-none focus:border-accent-primary"
          >
            <option value="all">All Status</option>
            <option value="completed">Completed</option>
            <option value="running">Running</option>
            <option value="failed">Failed</option>
            <option value="pending">Pending</option>
          </select>
        </div>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="card rounded-lg p-6"
      >
        <DataTable
          columns={[
            {
              key: 'project',
              header: 'Project',
              sortable: true,
              render: (s: Scan) => {
                const proj = projects?.find((p) => p.id === s.project_id);
                return (
                  <Link to={`/projects/${s.project_id}`} className="text-sm text-accent-primary hover:underline">
                    {proj?.name ?? s.project_id}
                  </Link>
                );
              },
            },
            {
              key: 'status',
              header: 'Status',
              sortable: true,
              render: (s: Scan) => <StatusBadge status={s.status} />,
            },
            {
              key: 'branch',
              header: 'Branch',
              sortable: true,
              render: (s: Scan) => (
                <span className="flex items-center gap-1 text-sm text-text-secondary font-mono">
                  <GitBranch className="w-3.5 h-3.5" />{s.branch}
                </span>
              ),
            },
            {
              key: 'risk_score',
              header: 'Risk Score',
              sortable: true,
              render: (s: Scan) => (
                s.risk_score != null ? (
                  <span className="text-sm font-medium" style={{ color: s.risk_score > 7 ? '#EF4444' : s.risk_score > 4 ? '#EAB308' : '#3B82F6' }}>
                    {s.risk_score.toFixed(1)}
                  </span>
                ) : <span className="text-sm text-text-tertiary">—</span>
              ),
            },
            {
              key: 'findings',
              header: 'Findings',
              sortable: true,
              render: (s: Scan) => (
                <span className="text-sm text-text-secondary">
                  {s.critical_count > 0 && <span className="text-red-400 mr-1">{s.critical_count}C</span>}
                  {s.high_count > 0 && <span className="text-orange-400 mr-1">{s.high_count}H</span>}
                  {s.medium_count > 0 && <span className="text-yellow-400 mr-1">{s.medium_count}M</span>}
                  {s.low_count > 0 && <span className="text-blue-400 mr-1">{s.low_count}L</span>}
                  {s.findings_count === 0 && <span>—</span>}
                </span>
              ),
            },
            {
              key: 'commit',
              header: 'Commit',
              render: (s: Scan) => (
                <span className="text-xs font-mono text-text-tertiary">{s.commit_sha?.slice(0, 7) ?? '—'}</span>
              ),
            },
            {
              key: 'date',
              header: 'Date',
              sortable: true,
              render: (s: Scan) => (
                <span className="text-sm text-text-secondary flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {s.completed_at ? new Date(s.completed_at).toLocaleDateString() : s.started_at ? new Date(s.started_at).toLocaleDateString() : '—'}
                </span>
              ),
            },
            {
              key: 'actions',
              header: '',
              render: (s: Scan) => (
                <Link to={`/scans/${s.id}`} className="p-1.5 rounded-md hover:bg-bg-surface-hover text-text-secondary hover:text-text-primary transition-colors">
                  <ArrowRight className="w-4 h-4" />
                </Link>
              ),
            },
          ]}
          data={filtered}
          keyExtractor={(s) => s.id}
          onRowClick={(s) => { window.location.href = `#/scans/${s.id}`; }}
        />
      </motion.div>
    </div>
  );
}
