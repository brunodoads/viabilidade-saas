'use client'

import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import type { OpportunityFilters, Recommendation } from '@/types'

interface FiltersBarProps {
  filters: OpportunityFilters
  onChange: (f: OpportunityFilters) => void
  total?: number
}

const RECOMMENDATIONS: { value: Recommendation | ''; label: string }[] = [
  { value: '', label: 'Todas' },
  { value: 'EXCELENTE', label: '★ Excelente' },
  { value: 'BOA', label: '◆ Boa' },
  { value: 'ARRISCADA', label: '▲ Arriscada' },
  { value: 'EVITAR', label: '✕ Evitar' },
]

export function FiltersBar({ filters, onChange, total }: FiltersBarProps) {
  return (
    <div className="flex flex-wrap items-end gap-4 p-4 bg-white border border-gray-200 rounded-lg">
      <div className="flex-1 min-w-[140px]">
        <Label>Score mínimo</Label>
        <Input
          type="number"
          min={0}
          max={100}
          placeholder="Ex: 60"
          value={filters.min_score ?? ''}
          onChange={(e) =>
            onChange({
              ...filters,
              min_score: e.target.value ? Number(e.target.value) : undefined,
            })
          }
        />
      </div>

      <div className="flex-1 min-w-[160px]">
        <Label>Classificação</Label>
        <select
          value={filters.recommendation ?? ''}
          onChange={(e) =>
            onChange({ ...filters, recommendation: (e.target.value as Recommendation) || '' })
          }
          className="flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {RECOMMENDATIONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </div>

      {total != null && (
        <div className="text-sm text-gray-500 pb-2 ml-auto">
          <span className="font-semibold text-gray-700">{total}</span> oportunidades
        </div>
      )}
    </div>
  )
}
