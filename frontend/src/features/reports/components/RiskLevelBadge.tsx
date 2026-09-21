import type { RiskLevel } from '../types'

const riskConfig: Record<RiskLevel, { label: string; classes: string }> = {
  critical: {
    label: 'Critical',
    classes: 'bg-red-500/10 text-red-400 border-red-900/50',
  },
  elevated: {
    label: 'Elevated',
    classes: 'bg-orange-500/10 text-orange-400 border-orange-900/50',
  },
  moderate: {
    label: 'Moderate',
    classes: 'bg-yellow-500/10 text-yellow-400 border-yellow-900/50',
  },
  low: {
    label: 'Low',
    classes: 'bg-emerald-500/10 text-emerald-400 border-emerald-900/50',
  },
}

interface RiskLevelBadgeProps {
  riskLevel: RiskLevel
}

export function RiskLevelBadge({ riskLevel }: RiskLevelBadgeProps) {
  const { label, classes } = riskConfig[riskLevel]

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${classes}`}
    >
      {label}
    </span>
  )
}