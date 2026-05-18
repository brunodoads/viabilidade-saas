'use client'

import { CheckCircle2, XCircle, Loader2, Clock } from 'lucide-react'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import type { CatalogStatusResponse } from '@/types'

interface PipelineStatusProps {
  data: CatalogStatusResponse
  className?: string
}

const STAGES = [
  { key: 'scout', label: 'Leitura do catálogo', description: 'Extraindo produtos do arquivo' },
  { key: 'market', label: 'Pesquisa de mercado', description: 'Buscando no Mercado Livre' },
  { key: 'finance', label: 'Análise financeira', description: 'Calculando margens' },
  { key: 'strategy', label: 'Score estratégico', description: 'Classificando oportunidades' },
]

function stageFromProgress(pct: number | null): number {
  if (!pct) return 0
  if (pct < 25) return 0
  if (pct < 50) return 1
  if (pct < 75) return 2
  if (pct < 100) return 3
  return 4
}

export function PipelineStatus({ data, className }: PipelineStatusProps) {
  const isError = data.status === 'ERROR'
  const isReady = data.status === 'READY'
  const isPending = data.status === 'PENDING'
  const isProcessing = data.status === 'PROCESSING'

  const progress = data.progress_pct ?? 0
  const currentStage = stageFromProgress(progress)

  return (
    <div className={cn('space-y-6', className)}>
      {/* Header */}
      <div className="flex items-center gap-3">
        {isError && <XCircle className="h-6 w-6 text-red-500 flex-shrink-0" />}
        {isReady && <CheckCircle2 className="h-6 w-6 text-green-500 flex-shrink-0" />}
        {(isPending || isProcessing) && (
          <Loader2 className="h-6 w-6 text-blue-500 animate-spin flex-shrink-0" />
        )}
        <div>
          <p className="font-semibold text-gray-900">
            {isError && 'Erro no processamento'}
            {isReady && 'Análise concluída!'}
            {isPending && 'Aguardando na fila...'}
            {isProcessing && 'Processando catálogo...'}
          </p>
          <p className="text-sm text-gray-500">{data.original_filename}</p>
        </div>
      </div>

      {/* Progress bar */}
      {(isProcessing || isReady) && (
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs text-gray-500">
            <span>
              {data.processed_products ?? 0} / {data.total_products ?? '?'} produtos
            </span>
            <span>{progress.toFixed(0)}%</span>
          </div>
          <Progress value={progress} />
        </div>
      )}

      {/* Error message */}
      {isError && data.error_message && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {data.error_message}
        </div>
      )}

      {/* Stages */}
      <div className="space-y-3">
        {STAGES.map((stage, idx) => {
          const done = isReady || currentStage > idx
          const active = isProcessing && currentStage === idx
          const pending = !done && !active

          return (
            <div key={stage.key} className="flex items-start gap-3">
              <div className="flex-shrink-0 mt-0.5">
                {done ? (
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                ) : active ? (
                  <Loader2 className="h-5 w-5 text-blue-500 animate-spin" />
                ) : (
                  <Clock className="h-5 w-5 text-gray-300" />
                )}
              </div>
              <div>
                <p className={cn('text-sm font-medium', done ? 'text-gray-800' : pending ? 'text-gray-400' : 'text-blue-600')}>
                  {stage.label}
                </p>
                <p className="text-xs text-gray-400">{stage.description}</p>
              </div>
            </div>
          )
        })}
      </div>

      {!isError && !isReady && (
        <p className="text-xs text-gray-400 text-center">Atualizando a cada 5 segundos...</p>
      )}
    </div>
  )
}
