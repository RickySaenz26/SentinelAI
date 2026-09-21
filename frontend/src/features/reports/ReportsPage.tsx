import { reports } from './mockData'
import { ReportCard } from './components/ReportCard'

export function ReportsPage() {
  const handleCreateReport = () => {
    // TODO: conectar con el backend cuando exista generación real de reportes.
    alert('[Mock] Se abriría el flujo de creación de un nuevo reporte.')
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-100 mb-1">Reports</h2>
          <p className="text-sm text-slate-400">
            {reports.length} reportes generados.
          </p>
        </div>
        <button
          type="button"
          onClick={handleCreateReport}
          className="bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-medium text-sm px-4 py-2 rounded-md transition-colors"
        >
          New Report
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {reports.map((report) => (
          <ReportCard key={report.id} report={report} />
        ))}
      </div>
    </div>
  )
}