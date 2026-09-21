import type { AiInsight } from '../types'
import { PriorityBadge } from './PriorityBadge'

interface InsightCardProps {
  insight: AiInsight
}

export function InsightCard({ insight }: InsightCardProps) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <h4 className="text-sm font-semibold text-slate-100">{insight.title}</h4>
        <PriorityBadge priority={insight.priority} />
      </div>

      <div className="flex flex-wrap gap-2">
        {insight.relatedCves.map((cve) => (
          <span
            key={cve}
            className="text-xs font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400"
          >
            {cve}
          </span>
        ))}
      </div>

      <div className="text-xs space-y-2">
        <div>
          <span className="text-slate-500 uppercase tracking-wide block mb-1">
            Technical Explanation
          </span>
          <p className="text-slate-300">{insight.technicalExplanation}</p>
        </div>
        <div>
          <span className="text-slate-500 uppercase tracking-wide block mb-1">
            Recommendation
          </span>
          <p className="text-slate-300">{insight.recommendation}</p>
        </div>
      </div>
    </div>
  )
}