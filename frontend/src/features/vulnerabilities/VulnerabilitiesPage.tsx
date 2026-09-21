import { vulnerabilities } from './mockData'
import { VulnerabilitiesTable } from './components/VulnerabilitiesTable'

export function VulnerabilitiesPage() {
  const criticalCount = vulnerabilities.filter((v) => v.severity === 'critical').length

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 mb-1">Vulnerabilities</h2>
        <p className="text-sm text-slate-400">
          {vulnerabilities.length} vulnerabilidades detectadas — {criticalCount} críticas.
          Haz clic en una fila para ver el detalle.
        </p>
      </div>

      <VulnerabilitiesTable vulnerabilities={vulnerabilities} />
    </div>
  )
}