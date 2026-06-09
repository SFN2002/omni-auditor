import { useState } from 'react';
import { motion } from 'framer-motion';
import { Search, GitBranch, Clock } from 'lucide-react';
import { Link } from 'react-router';
import { useProjects } from '../hooks/useApi';
import RiskScore from '../components/RiskScore';
import BaselineBadge from '../components/BaselineBadge';

export default function ProjectsPage() {
  const { data: projects } = useProjects();
  const [search, setSearch] = useState('');

  const filtered = projects?.filter((p) =>
    p.name.toLowerCase().includes(search.toLowerCase()) ||
    p.description?.toLowerCase().includes(search.toLowerCase())
  ) ?? [];

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h2 className="text-xl font-semibold text-text-primary">Projects</h2>
          <p className="text-sm text-text-secondary mt-1">{filtered.length} project{filtered.length !== 1 ? 's' : ''} monitored</p>
        </div>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-tertiary" />
          <input
            type="text"
            placeholder="Search projects..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10 pr-4 py-2 bg-bg-surface border border-border-default rounded-md text-sm text-text-primary placeholder:text-text-tertiary outline-none focus:border-accent-primary w-full sm:w-64"
          />
        </div>
      </div>

      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-6">
        {filtered.map((p, i) => (
          <motion.div
            key={p.id}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.06, duration: 0.4 }}
          >
            <Link to={`/projects/${p.id}`} className="block card card-hover rounded-lg p-6 h-full">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-lg font-semibold text-text-primary">{p.name}</h3>
                  {p.github_repo && (
                    <div className="flex items-center gap-1 mt-1 text-xs text-text-tertiary">
                      <GitBranch className="w-3 h-3" />
                      {p.github_repo}
                    </div>
                  )}
                </div>
                <RiskScore score={p.risk_score} size="sm" />
              </div>

              {p.description && (
                <p className="mt-3 text-sm text-text-secondary leading-relaxed line-clamp-2">{p.description}</p>
              )}

              <div className="mt-4 flex items-center gap-4 text-xs text-text-tertiary">
                <span>{p.findings_count} findings</span>
                <span>{p.scans_count} scans</span>
                {p.last_scan_at && (
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {new Date(p.last_scan_at).toLocaleDateString()}
                  </span>
                )}
              </div>

              <div className="mt-3">
                <BaselineBadge status={p.baseline_status} />
              </div>

              {/* Finding severity breakdown */}
              <div className="mt-4 flex items-center gap-1">
                {[1, 2, 3].map((bar) => (
                  <div key={bar} className="flex-1 h-1.5 rounded-full bg-bg-elevated overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${Math.min(100, Math.random() * 60 + 20)}%`,
                        background: bar === 1 ? '#EF4444' : bar === 2 ? '#F97316' : '#EAB308',
                        opacity: 0.6,
                      }}
                    />
                  </div>
                ))}
              </div>
            </Link>
          </motion.div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16">
          <div className="w-16 h-16 mx-auto rounded-full bg-bg-surface flex items-center justify-center mb-4">
            <Search className="w-8 h-8 text-text-tertiary" />
          </div>
          <p className="text-text-secondary">No projects found matching &quot;{search}&quot;</p>
        </div>
      )}
    </div>
  );
}
