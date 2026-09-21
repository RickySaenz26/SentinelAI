import { useState } from 'react'
import { scanTypes } from '../mockData'
import type { ScanType } from '../types'
import { ScanTypeCard } from './ScanTypeCard'
import { assets } from '../../assets/mockData'

export function NewScanForm() {
  const [selectedType, setSelectedType] = useState<ScanType>('quick')
  const [selectedAsset, setSelectedAsset] = useState(assets[0]?.id ?? '')

  const handleLaunch = () => {
    // TODO: conectar con la API/backend cuando exista el Security Orchestrator.
    // Por ahora esto es solo una interfaz preparada, no ejecuta ningún escaneo real.
    alert(
      `[Mock] Se lanzaría un ${selectedType} scan sobre el asset seleccionado. Esto se conectará al backend más adelante.`
    )
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5 space-y-4">
      <div>
        <label className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-2 block">
          Target Asset
        </label>
        <select
          value={selectedAsset}
          onChange={(e) => setSelectedAsset(e.target.value)}
          className="w-full bg-slate-950 border border-slate-800 rounded-md px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/50"
        >
          {assets.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.ip} ({asset.hostname})
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-2 block">
          Scan Type
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {scanTypes.map((scanType) => (
            <ScanTypeCard
              key={scanType.id}
              scanType={scanType}
              selected={selectedType === scanType.id}
              onSelect={() => setSelectedType(scanType.id)}
            />
          ))}
        </div>
      </div>

      <button
        type="button"
        onClick={handleLaunch}
        className="bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-medium text-sm px-4 py-2 rounded-md transition-colors"
      >
        Launch Scan
      </button>
    </div>
  )
}