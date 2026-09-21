import { scanJobs } from './mockData'
import { NewScanForm } from './components/NewScanForm'
import { ScansHistoryTable } from './components/ScansHistoryTable'

export function ScansPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 mb-1">Scans</h2>
        <p className="text-sm text-slate-400">
          Lanza nuevos escaneos y consulta el historial de auditorías.
        </p>
      </div>

      <NewScanForm />

      <div>
        <h3 className="text-sm font-semibold text-slate-300 mb-3">Scan History</h3>
        <ScansHistoryTable jobs={scanJobs} />
      </div>
    </div>
  )
}