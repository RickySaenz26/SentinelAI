import type { ScanStatus } from '../types'

const statusConfig: Record<ScanStatus, { label: string; classes: string }> = {
  queued: {
    label: 'Queued',
    classes: 'bg-slate-500/10 text-slate-400 border-slate-800',
  },
  running: {
    label: 'Running',
    classes: 'bg-cyan-500/10 text-cyan-400 border-cyan-900/50',
  },
  completed: {
    label: 'Completed',
    classes: 'bg-emerald-500/10 text-emerald-400 border-emerald-900/50',
  },
  failed: {
    label: 'Failed',
    classes: 'bg-red-500/10 text-red-400 border-red-900/50',
  },
}

interface ScanStatusBadgeProps {
  status: ScanStatus
}

export function ScanStatusBadge({ status }: ScanStatusBadgeProps) {
  const { label, classes } = statusConfig[status]

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${classes}`}
    >
      {label}
    </span>
  )
}