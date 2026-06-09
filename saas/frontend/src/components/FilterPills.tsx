import type { Severity } from '../types';

interface FilterPillsProps {
  active: Severity | 'all';
  onChange: (s: Severity | 'all') => void;
  counts?: Record<Severity | 'all', number>;
}

const PILLS: { key: Severity | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'high', label: 'High' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
  { key: 'info', label: 'Info' },
];

export default function FilterPills({ active, onChange, counts }: FilterPillsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {PILLS.map((pill) => {
        const isActive = active === pill.key;
        return (
          <button
            key={pill.key}
            onClick={() => onChange(pill.key)}
            className={`
              px-4 py-1.5 rounded-full text-sm font-medium transition-all duration-200
              ${isActive
                ? 'bg-accent-primary text-white border-transparent'
                : 'bg-transparent text-text-secondary border border-border-default hover:bg-bg-surface-hover hover:text-text-primary'
              }
            `}
          >
            {pill.label}
            {counts && counts[pill.key] !== undefined && (
              <span className="ml-1.5 opacity-70">({counts[pill.key]})</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
