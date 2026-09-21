import { useState } from 'react'
import type { Vulnerability, VulnerabilityStatus } from '../types'
import { SeverityBadge } from './SeverityBadge'

interface VulnerabilitiesTableProps {
  vulnerabilities: Vulnerability[]
}

const statusLabels: Record<VulnerabilityStatus, string> = {
  open: 'Open',
  in_progress: 'In Progress',
  resolved: 'Resolved',
  accepted_risk: 'Accepted Risk',
}

export function VulnerabilitiesTable({ vulnerabilities }: VulnerabilitiesTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  if (vulnerabilities.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-8 text-center">
        <p className="text-sm text-slate-400">No vulnerabilities detected.</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-left">
            <th className="px-4 py-3 font-medium text-slate-400">CVE</th>
            <th className="px-4 py-3 font-medium text-slate-400">Service</th>
            <th className="px-4 py-3 font-medium text-slate-400">Severity</th>
            <th className="px-4 py-3 font-medium text-slate-400">CVSS</th>
            <th className="px-4 py-3 font-medium text-slate-400">Exploit</th>
            <th className="px-4 py-3 font-medium text-slate-400">Status</th>
          </tr>
        </thead>
        <tbody>
          {vulnerabilities.map((vuln) => {
            const isExpanded = expandedId === vuln.id
            return (
              <>
                <tr
                  key={vuln.id}
                  onClick={() => setExpandedId(isExpanded ? null : vuln.id)}
                  className="border-b border-slate-800/60 hover:bg-slate-800/30 transition-colors cursor-pointer"
                >
                  <td className="px-4 py-3 font-mono text-slate-200">{vuln.cve}</td>
                  <td className="px-4 py-3 text-slate-400">{vuln.service}</td>
                  <td className="px-4 py-3">
                    <SeverityBadge severity={vuln.severity} />
                  </td>
                  <td className="px-4 py-3 text-slate-300 font-mono">{vuln.cvss}</td>
                  <td className="px-4 py-3">
                    {vuln.exploitAvailable ? (
                      <span className="text-red-400 text-xs font-medium">Yes</span>
                    ) : (
                      <span className="text-slate-500 text-xs">No</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-400">{statusLabels[vuln.status]}</td>
                </tr>
                {isExpanded && (
                  <tr key={`${vuln.id}-detail`} className="border-b border-slate-800/60 bg-slate-950/40">
                    <td colSpan={6} className="px-4 py-4">
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
                        <div>
                          <span className="text-slate-500 uppercase tracking-wide block mb-1">
                            Evidence
                          </span>
                          <p className="text-slate-300">{vuln.evidence}</p>
                        </div>
                        <div>
                          <span className="text-slate-500 uppercase tracking-wide block mb-1">
                            Recommendation
                          </span>
                          <p className="text-slate-300">{vuln.recommendation}</p>
                        </div>
                        <div>
                          <span className="text-slate-500 uppercase tracking-wide block mb-1">
                            Source
                          </span>
                          <p className="text-slate-300">{vuln.source}</p>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}