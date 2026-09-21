import { executiveSummary, aiInsights } from './mockData'
import { ExecutiveSummaryCard } from './components/ExecutiveSummaryCard'
import { InsightCard } from './components/InsightCard'

export function AiAnalysisPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 mb-1">AI Analysis</h2>
        <p className="text-sm text-slate-400">
          Correlación de vulnerabilidades y recomendaciones generadas por IA.
        </p>
      </div>

      <ExecutiveSummaryCard summary={executiveSummary} />

      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">Correlated Insights</h3>
        {aiInsights.map((insight) => (
          <InsightCard key={insight.id} insight={insight} />
        ))}
      </div>
    </div>
  )
}