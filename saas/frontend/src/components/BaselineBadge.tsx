import { TrendingUp, TrendingDown, Minus, Zap } from 'lucide-react';
import type { BaselineStatus } from '../types';

interface BaselineBadgeProps {
  status?: BaselineStatus;
  className?: string;
}

export default function BaselineBadge({ status, className = '' }: BaselineBadgeProps) {
  if (!status) return null;

  const config: Record<BaselineStatus, { icon: React.ReactNode; text: string; cls: string }> = {
    IMPROVED: { icon: <TrendingUp className="w-3 h-3" />, text: 'IMPROVED', cls: 'bg-green-500/15 text-green-400 border-green-500/30' },
    STABLE: { icon: <Minus className="w-3 h-3" />, text: 'STABLE', cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30' },
    DEGRADED: { icon: <TrendingDown className="w-3 h-3" />, text: 'DEGRADED', cls: 'bg-orange-500/15 text-orange-400 border-orange-500/30' },
    FRACTURED: { icon: <Zap className="w-3 h-3" />, text: 'FRACTURED', cls: 'bg-red-500/15 text-red-400 border-red-500/30' },
  };

  const c = config[status];
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-sm text-xs font-semibold border ${c.cls} ${className}`}>
      {c.icon}
      {c.text}
    </span>
  );
}
