import type { Severity } from '../types'

const severityConfig: Record<Severity, { label: string; classes: string }> = {
  critical: {
    label: 'Critical',
    classes: 'bg-red-500/10 text-red-400 border-red-900/50',
  },
  high: {
    label: 'High',
    classes: 'bg-orange-500/10 text-orange-400 border-orange-900/50',
  },
  medium: {
    label: 'Medium',
    classes: 'bg-yellow-500/10 text-yellow-400 border-yellow-900/50',
  },
  low: {
    label: 'Low',
    classes: 'bg-slate-500/10 text-slate-400 border-slate-800',
  },
}

interface SeverityBadgeProps {
  severity: Severity
}

export function SeverityBadge({ severity }: SeverityBadgeProps) {
  const { label, classes } = severityConfig[severity]

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${classes}`}
    >
      {label}
    </span>
  )
}