import type { ExecutiveSummary } from '../types'

interface ExecutiveSummaryCardProps {
  summary: ExecutiveSummary
}

export function ExecutiveSummaryCard({ summary }: ExecutiveSummaryCardProps) {
  return (
    <div className="rounded-lg border border-cyan-900/50 bg-cyan-500/5 p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium text-cyan-400 uppercase tracking-wide">
          AI Executive Summary
        </span>
        <span className="text-xs text-slate-500">Generated {summary.generatedAt}</span>
      </div>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs text-slate-400">Overall Risk Level:</span>
        <span className="text-sm font-semibold text-orange-400">
          {summary.overallRiskLevel}
        </span>
      </div>
      <p className="text-sm text-slate-300 leading-relaxed">{summary.summary}</p>
    </div>
  )
}