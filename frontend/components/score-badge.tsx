import { cn } from '@/lib/utils'
import type { Recommendation } from '@/types'

const CONFIG: Record<Recommendation, { label: string; className: string }> = {
  EXCELENTE: {
    label: '★ EXCELENTE',
    className: 'bg-green-100 text-green-800 border border-green-200',
  },
  BOA: {
    label: '◆ BOA',
    className: 'bg-blue-100 text-blue-800 border border-blue-200',
  },
  ARRISCADA: {
    label: '▲ ARRISCADA',
    className: 'bg-amber-100 text-amber-800 border border-amber-200',
  },
  EVITAR: {
    label: '✕ EVITAR',
    className: 'bg-red-100 text-red-800 border border-red-200',
  },
}

interface ScoreBadgeProps {
  recommendation: Recommendation
  score?: number
  className?: string
}

export function ScoreBadge({ recommendation, score, className }: ScoreBadgeProps) {
  const cfg = CONFIG[recommendation]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold tracking-wide',
        cfg.className,
        className,
      )}
    >
      {cfg.label}
      {score != null && (
        <span className="opacity-70 font-normal">({Number(score).toFixed(0)})</span>
      )}
    </span>
  )
}
