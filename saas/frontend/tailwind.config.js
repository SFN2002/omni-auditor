/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-deep': '#0A0E27',
        'bg-base': '#111827',
        'bg-surface': '#1E293B',
        'bg-surface-hover': '#243047',
        'bg-elevated': '#0F172A',
        'text-primary': '#F8FAFC',
        'text-secondary': '#94A3B8',
        'text-tertiary': '#64748B',
        'accent-primary': '#3B82F6',
        'accent-primary-hover': '#2563EB',
        'accent-secondary': '#6366F1',
        'accent-glow': 'rgba(59,130,246,0.15)',
        'border-default': '#334155',
        'border-hover': '#475569',
        'border-focus': '#3B82F6',
        'sev-critical': '#EF4444',
        'sev-high': '#F97316',
        'sev-medium': '#EAB308',
        'sev-low': '#3B82F6',
        'sev-info': '#6B7280',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      borderRadius: {
        sm: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
        full: '9999px',
      },
      keyframes: {
        fadeInUp: {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      animation: {
        fadeInUp: 'fadeInUp 0.4s ease-out forwards',
        shimmer: 'shimmer 1.5s linear infinite',
      },
    },
  },
  plugins: [],
}
