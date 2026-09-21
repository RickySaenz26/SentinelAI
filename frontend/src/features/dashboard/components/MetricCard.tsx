import type { DashboardMetric, MetricTone } from '../types'

const toneStyles: Record<MetricTone, { border: string; value: string; dot: string }> = {
  neutral: {
    border: 'border-slate-800',
    value: 'text-slate-100',
    dot: 'bg-slate-500',
  },
  critical: {
    border: 'border-red-900/50',
    value: 'text-red-400',
    dot: 'bg-red-500',
  },
  high: {
    border: 'border-orange-900/50',
    value: 'text-orange-400',
    dot: 'bg-orange-500',
  },
  success: {
    border: 'border-emerald-900/50',
    value: 'text-emerald-400',
    dot: 'bg-emerald-500',
  },
  info: {
    border: 'border-cyan-900/50',
    value: 'text-cyan-400',
    dot: 'bg-cyan-500',
  },
}

interface MetricCardProps {
  metric: DashboardMetric
}

export function MetricCard({ metric }: MetricCardProps) {
  const styles = toneStyles[metric.tone]

  return (
    <div
      className={`rounded-lg border ${styles.border} bg-slate-900/40 p-5 flex flex-col gap-2`}
    >
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${styles.dot}`} />
        <span className="text-xs font-medium text-slate-400 uppercase tracking-wide">
          {metric.label}
        </span>
      </div>
      <span className={`text-3xl font-bold ${styles.value}`}>{metric.value}</span>
      {metric.hint && (
        <span className="text-xs text-slate-500">{metric.hint}</span>
      )}
    </div>
  )
}