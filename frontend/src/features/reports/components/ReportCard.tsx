import type { Report } from '../types'
import { RiskLevelBadge } from './RiskLevelBadge'

interface ReportCardProps {
  report: Report
}

export function ReportCard({ report }: ReportCardProps) {
  const handleView = () => {
    // TODO: conectar con el backend cuando exista generación real de reportes.
    alert(`[Mock] Se abriría el reporte "${report.title}".`)
  }

  const handleExport = () => {
    // TODO: conectar con el backend para exportar a PDF/DOCX.
    alert(`[Mock] Se exportaría "${report.title}" como PDF.`)
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-slate-100">{report.title}</h4>
          <p className="text-xs text-slate-500 mt-0.5">{report.scope}</p>
        </div>
        <RiskLevelBadge riskLevel={report.riskLevel} />
      </div>

      <div className="flex items-center gap-4 text-xs text-slate-400">
        <span>{report.createdAt}</span>
        <span>•</span>
        <span>{report.findingsCount} findings</span>
        <span>•</span>
        <span className={report.status === 'draft' ? 'text-yellow-400' : 'text-emerald-400'}>
          {report.status === 'draft' ? 'Draft' : 'Final'}
        </span>
      </div>

      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={handleView}
          className="text-xs font-medium px-3 py-1.5 rounded-md bg-slate-800 text-slate-200 hover:bg-slate-700 transition-colors"
        >
          View Report
        </button>
        <button
          type="button"
          onClick={handleExport}
          className="text-xs font-medium px-3 py-1.5 rounded-md border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors"
        >
          Export PDF
        </button>
      </div>
    </div>
  )
}