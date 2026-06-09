import { useEffect, useState } from 'react';

interface RiskScoreProps {
  score: number;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
}

function getColor(score: number): string {
  if (score <= 3.9) return '#22C55E';
  if (score <= 6.9) return '#EAB308';
  if (score <= 8.9) return '#F97316';
  return '#EF4444';
}

const SIZE_MAP = {
  sm: { w: 48, stroke: 4, font: 14 },
  md: { w: 72, stroke: 5, font: 20 },
  lg: { w: 120, stroke: 8, font: 32 },
};

export default function RiskScore({ score, size = 'md', showLabel = false }: RiskScoreProps) {
  const [animatedScore, setAnimatedScore] = useState(0);
  const { w, stroke, font } = SIZE_MAP[size];
  const radius = (w - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const color = getColor(score);
  const dashOffset = circumference - (animatedScore / 10) * circumference;

  useEffect(() => {
    let frame: number;
    const start = performance.now();
    const duration = 800;
    const animate = (now: number) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setAnimatedScore(Math.round(score * eased * 10) / 10);
      if (progress < 1) frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [score]);

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={w} height={w} className="transform -rotate-90">
        <circle
          cx={w / 2} cy={w / 2} r={radius}
          fill="none" stroke="#1E293B" strokeWidth={stroke}
        />
        <circle
          cx={w / 2} cy={w / 2} r={radius}
          fill="none" stroke={color} strokeWidth={stroke}
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 0.8s cubic-bezier(0, 0, 0.2, 1)' }}
        />
        <text
          x={w / 2} y={w / 2}
          textAnchor="middle"
          dominantBaseline="central"
          className="transform rotate-90"
          fill={color}
          fontSize={font}
          fontWeight={700}
          fontFamily="Inter, sans-serif"
        >
          {animatedScore.toFixed(1)}
        </text>
      </svg>
      {showLabel && (
        <span className="text-xs text-text-tertiary uppercase tracking-wider">
          {score <= 3.9 ? 'Low' : score <= 6.9 ? 'Medium' : score <= 8.9 ? 'High' : 'Critical'}
        </span>
      )}
    </div>
  );
}
