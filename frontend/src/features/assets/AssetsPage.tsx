import { assets } from './mockData'
import { AssetsTable } from './components/AssetsTable'

export function AssetsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 mb-1">Assets</h2>
        <p className="text-sm text-slate-400">
          {assets.length} assets registrados en el inventario.
        </p>
      </div>

      <AssetsTable assets={assets} />
    </div>
  )
}