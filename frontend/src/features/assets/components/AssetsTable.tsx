import type { Asset } from '../types'
import { StatusBadge } from '../../../components/ui/StatusBadge'

interface AssetsTableProps {
  assets: Asset[]
}

export function AssetsTable({ assets }: AssetsTableProps) {
  if (assets.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-8 text-center">
        <p className="text-sm text-slate-400">No assets discovered yet.</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-left">
            <th className="px-4 py-3 font-medium text-slate-400">IP Address</th>
            <th className="px-4 py-3 font-medium text-slate-400">Hostname</th>
            <th className="px-4 py-3 font-medium text-slate-400">OS</th>
            <th className="px-4 py-3 font-medium text-slate-400">Ports</th>
            <th className="px-4 py-3 font-medium text-slate-400">Services</th>
            <th className="px-4 py-3 font-medium text-slate-400">Status</th>
            <th className="px-4 py-3 font-medium text-slate-400">Last Scan</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => (
            <tr
              key={asset.id}
              className="border-b border-slate-800/60 last:border-0 hover:bg-slate-800/30 transition-colors"
            >
              <td className="px-4 py-3 font-mono text-slate-200">{asset.ip}</td>
              <td className="px-4 py-3 text-slate-200">{asset.hostname}</td>
              <td className="px-4 py-3 text-slate-400">{asset.os}</td>
              <td className="px-4 py-3 text-slate-400 font-mono text-xs">
                {asset.ports.join(', ')}
              </td>
              <td className="px-4 py-3 text-slate-400">{asset.services.join(', ')}</td>
              <td className="px-4 py-3">
                <StatusBadge status={asset.status} />
              </td>
              <td className="px-4 py-3 text-slate-500">{asset.lastScan}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}