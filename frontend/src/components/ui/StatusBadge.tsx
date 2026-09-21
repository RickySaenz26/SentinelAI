export type StatusKind = 'online' | 'offline' | 'unknown'

const statusConfig: Record<StatusKind, { label: string; classes: string }> = {
  online: {
    label: 'Online',
    classes: 'bg-emerald-500/10 text-emerald-400 border-emerald-900/50',
  },
  offline: {
    label: 'Offline',
    classes: 'bg-red-500/10 text-red-400 border-red-900/50',
  },
  unknown: {
    label: 'Unknown',
    classes: 'bg-slate-500/10 text-slate-400 border-slate-800',
  },
}

interface StatusBadgeProps {
  status: StatusKind
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const { label, classes } = statusConfig[status]

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${classes}`}
    >
      {label}
    </span>
  )
}