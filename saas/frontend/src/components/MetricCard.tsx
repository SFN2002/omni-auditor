import { type LucideIcon } from 'lucide-react';
import { motion } from 'framer-motion';

interface MetricCardProps {
  icon: LucideIcon;
  iconColor?: string;
  value: string | number;
  label: string;
  secondary?: string;
  secondaryColor?: string;
  trend?: 'up' | 'down';
  delay?: number;
  children?: React.ReactNode;
}

export default function MetricCard({
  icon: Icon,
  iconColor = 'text-accent-primary',
  value,
  label,
  secondary,
  secondaryColor = 'text-text-tertiary',
  delay = 0,
  children,
}: MetricCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: delay * 0.06, ease: [0, 0, 0.2, 1] }}
      className="card card-hover p-6 rounded-lg"
    >
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <Icon className={`w-5 h-5 ${iconColor} mb-3`} />
          <div className="text-4xl font-bold text-text-primary tracking-tight">{value}</div>
          <div className="text-xs uppercase tracking-wider text-text-tertiary mt-1">{label}</div>
          {secondary && (
            <div className={`text-sm mt-2 ${secondaryColor}`}>{secondary}</div>
          )}
        </div>
        {children && <div className="ml-4">{children}</div>}
      </div>
    </motion.div>
  );
}
