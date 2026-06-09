import type { Severity } from '../types';

const SEVERITY_CONFIG: Record<Severity, string> = {
  critical: 'severity-critical',
  high: 'severity-high',
  medium: 'severity-medium',
  low: 'severity-low',
  info: 'severity-info',
};

interface SeverityBadgeProps {
  severity: Severity;
  className?: string;
}

export default function SeverityBadge({ severity, className = '' }: SeverityBadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-sm text-xs font-semibold uppercase tracking-wide border ${SEVERITY_CONFIG[severity]} ${className}`}
    >
      {severity}
    </span>
  );
}
