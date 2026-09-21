import type { Priority } from '../types'

const priorityConfig: Record<Priority, { label: string; classes: string }> = {
  urgent: {
    label: 'Urgent',
    classes: 'bg-red-500/10 text-red-400 border-red-900/50',
  },
  high: {
    label: 'High',
    classes: 'bg-orange-500/10 text-orange-400 border-orange-900/50',
  },
  moderate: {
    label: 'Moderate',
    classes: 'bg-yellow-500/10 text-yellow-400 border-yellow-900/50',
  },
  low: {
    label: 'Low',
    classes: 'bg-slate-500/10 text-slate-400 border-slate-800',
  },
}

interface PriorityBadgeProps {
  priority: Priority
}

export function PriorityBadge({ priority }: PriorityBadgeProps) {
  const { label, classes } = priorityConfig[priority]

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${classes}`}
    >
      {label}
    </span>
  )
}