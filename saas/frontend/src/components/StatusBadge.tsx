import type { ScanStatus } from '../types';

interface StatusBadgeProps {
  status: ScanStatus;
  className?: string;
}

export default function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  const config: Record<ScanStatus, { dot: string; text: string; animate?: boolean }> = {
    pending: { dot: 'bg-gray-500', text: 'text-gray-400' },
    running: { dot: 'bg-blue-500', text: 'text-blue-400', animate: true },
    completed: { dot: 'bg-green-500', text: 'text-green-400' },
    failed: { dot: 'bg-red-500', text: 'text-red-400' },
  };
  const c = config[status];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${c.text} ${className}`}>
      <span className={`w-2 h-2 rounded-full ${c.dot} ${c.animate ? 'animate-pulse' : ''}`} />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}
