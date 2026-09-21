import type { ScanJob } from '../types'
import { ScanStatusBadge } from './ScanStatusBadge'

interface ScansHistoryTableProps {
  jobs: ScanJob[]
}

const typeLabels: Record<ScanJob['type'], string> = {
  quick: 'Quick',
  standard: 'Standard',
  deep: 'Deep',
}

export function ScansHistoryTable({ jobs }: ScansHistoryTableProps) {
  if (jobs.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-8 text-center">
        <p className="text-sm text-slate-400">No scans have been run yet.</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-left">
            <th className="px-4 py-3 font-medium text-slate-400">Target</th>
            <th className="px-4 py-3 font-medium text-slate-400">Type</th>
            <th className="px-4 py-3 font-medium text-slate-400">Status</th>
            <th className="px-4 py-3 font-medium text-slate-400">Progress</th>
            <th className="px-4 py-3 font-medium text-slate-400">Started</th>
            <th className="px-4 py-3 font-medium text-slate-400">Findings</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr
              key={job.id}
              className="border-b border-slate-800/60 last:border-0 hover:bg-slate-800/30 transition-colors"
            >
              <td className="px-4 py-3 text-slate-200">{job.target}</td>
              <td className="px-4 py-3 text-slate-400">{typeLabels[job.type]}</td>
              <td className="px-4 py-3">
                <ScanStatusBadge status={job.status} />
              </td>
              <td className="px-4 py-3">
                <div className="w-24 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-cyan-500"
                    style={{ width: `${job.progress}%` }}
                  />
                </div>
              </td>
              <td className="px-4 py-3 text-slate-500">{job.startedAt}</td>
              <td className="px-4 py-3 text-slate-400">
                {job.findings === null ? '—' : job.findings}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}