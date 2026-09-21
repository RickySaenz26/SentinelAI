import type { ScanTypeDefinition } from '../types'

interface ScanTypeCardProps {
  scanType: ScanTypeDefinition
  selected: boolean
  onSelect: () => void
}

export function ScanTypeCard({ scanType, selected, onSelect }: ScanTypeCardProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`text-left rounded-lg border p-4 transition-colors ${
        selected
          ? 'border-cyan-500/50 bg-cyan-500/5'
          : 'border-slate-800 bg-slate-900/40 hover:border-slate-700'
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <span
          className={`text-sm font-semibold ${
            selected ? 'text-cyan-400' : 'text-slate-100'
          }`}
        >
          {scanType.label}
        </span>
        <span className="text-xs text-slate-500">{scanType.duration}</span>
      </div>
      <p className="text-xs text-slate-400 mb-2">{scanType.description}</p>
      <code className="text-xs font-mono text-slate-500 block truncate">
        {scanType.command}
      </code>
    </button>
  )
}