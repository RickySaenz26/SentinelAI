import { dashboardMetrics } from './mockData'
import { MetricCard } from './components/MetricCard'

export function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 mb-1">Dashboard</h2>
        <p className="text-sm text-slate-400">
          Resumen general de seguridad de tu infraestructura.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {dashboardMetrics.map((metric) => (
          <MetricCard key={metric.id} metric={metric} />
        ))}
      </div>
    </div>
  )
}