'use client'

import { CheckCircle2, XCircle, Loader2, Clock, FileSearch, Search, Calculator, Trophy } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { CatalogStatus, CatalogStatusResponse } from '@/types'

interface PipelineStatusProps {
  data: CatalogStatusResponse
  className?: string
}

// Ordem real das etapas do pipeline backend
const STATUS_ORDER: CatalogStatus[] = ['PARSING', 'RESEARCHING', 'ANALYZING', 'SCORING']

const STAGES = [
  {
    status: 'PARSING' as CatalogStatus,
    label: 'Leitura do catálogo',
    description: 'Extraindo e normalizando produtos do arquivo',
    activeDetail: 'Identificando produtos, preços e SKUs...',
    Icon: FileSearch,
  },
  {
    status: 'RESEARCHING' as CatalogStatus,
    label: 'Pesquisa no Mercado Livre',
    description: 'Buscando anúncios, preços e volume de vendas',
    activeDetail: 'Esta etapa pode levar alguns minutos dependendo do tamanho do catálogo.',
    Icon: Search,
  },
  {
    status: 'ANALYZING' as CatalogStatus,
    label: 'Análise financeira',
    description: 'Calculando margens, break-even e viabilidade',
    activeDetail: 'Calculando margem bruta, taxa ML e ponto de equilíbrio...',
    Icon: Calculator,
  },
  {
    status: 'SCORING' as CatalogStatus,
    label: 'Score estratégico',
    description: 'Classificando e rankeando oportunidades',
    activeDetail: 'Gerando score final e recomendações...',
    Icon: Trophy,
  },
]

/** Retorna quantas etapas estão concluídas com base no status atual */
function getCompletedCount(status: CatalogStatus): number {
  if (status === 'READY') return 4
  if (status === 'ERROR' || status === 'PENDING') return 0
  const idx = STATUS_ORDER.indexOf(status)
  return idx === -1 ? 0 : idx // etapas antes do status atual estão concluídas
}

/** Retorna a % de progresso global para a barra superior */
function getOverallProgress(status: CatalogStatus): number {
  switch (status) {
    case 'PENDING':     return 0
    case 'PARSING':     return 10
    case 'RESEARCHING': return 30
    case 'ANALYZING':   return 70
    case 'SCORING':     return 88
    case 'READY':       return 100
    default:            return 0
  }
}

/** Label de tempo estimado por etapa */
function getTimeHint(status: CatalogStatus, totalProducts: number | null): string | null {
  if (status === 'RESEARCHING') {
    if (totalProducts && totalProducts > 50) return `~${Math.ceil(totalProducts / 30)} min`
    return '~2-5 min'
  }
  return null
}

export function PipelineStatus({ data, className }: PipelineStatusProps) {
  const { status, error_message, total_products, processed_products } = data

  const isError   = status === 'ERROR'
  const isReady   = status === 'READY'
  const isPending = status === 'PENDING'
  const isActive  = !isError && !isReady && !isPending

  const completedCount = getCompletedCount(status)
  const overallPct     = getOverallProgress(status)
  const timeHint       = getTimeHint(status, total_products)

  return (
    <div className={cn('space-y-5', className)}>

      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        {isError   && <XCircle      className="h-6 w-6 text-red-500   flex-shrink-0" />}
        {isReady   && <CheckCircle2 className="h-6 w-6 text-green-500 flex-shrink-0" />}
        {isPending && <Clock        className="h-6 w-6 text-gray-400  flex-shrink-0" />}
        {isActive  && <Loader2      className="h-6 w-6 text-blue-500  flex-shrink-0 animate-spin" />}

        <div>
          <p className="font-semibold text-gray-900">
            {isError   && 'Erro no processamento'}
            {isReady   && 'Análise concluída!'}
            {isPending && 'Aguardando na fila...'}
            {status === 'PARSING'     && 'Lendo catálogo...'}
            {status === 'RESEARCHING' && 'Pesquisando no Mercado Livre...'}
            {status === 'ANALYZING'   && 'Analisando viabilidade financeira...'}
            {status === 'SCORING'     && 'Gerando score final...'}
          </p>
          <p className="text-sm text-gray-500">{data.original_filename}</p>
        </div>

        {/* Contagem de produtos (quando disponível) */}
        {total_products != null && (
          <div className="ml-auto text-right flex-shrink-0">
            <p className="text-sm font-medium text-gray-700">
              {isReady ? total_products : (processed_products ?? 0)} / {total_products}
            </p>
            <p className="text-xs text-gray-400">produtos</p>
          </div>
        )}
      </div>

      {/* ── Barra de progresso global ───────────────────────────── */}
      {!isPending && (
        <div className="space-y-1">
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-700 ease-out',
                isError  ? 'bg-red-400'   :
                isReady  ? 'bg-green-500' :
                'bg-blue-500'
              )}
              style={{ width: `${overallPct}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-gray-400">
            <span>
              {isReady   && 'Concluído'}
              {isError   && 'Falhou'}
              {isActive  && `Etapa ${completedCount + 1} de 4`}
              {isPending && 'Na fila'}
            </span>
            <span>{overallPct}%</span>
          </div>
        </div>
      )}

      {/* ── Stepper por etapa ───────────────────────────────────── */}
      <div className="space-y-0">
        {STAGES.map((stage, idx) => {
          const isDone   = isReady || completedCount > idx
          const isStageActive = isActive && STATUS_ORDER.indexOf(status) === idx
          const isStageError  = isError  && STATUS_ORDER.indexOf(status) === idx
          const isPendingStage = !isDone && !isStageActive && !isStageError

          const { Icon } = stage
          const timeLabel = isStageActive ? getTimeHint(status, total_products) : null

          return (
            <div key={stage.status} className="flex gap-3">
              {/* Linha vertical + ícone */}
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 transition-colors',
                    isDone         ? 'bg-green-100' :
                    isStageActive  ? 'bg-blue-100'  :
                    isStageError   ? 'bg-red-100'   :
                    'bg-gray-50'
                  )}
                >
                  {isDone && (
                    <CheckCircle2 className="h-4 w-4 text-green-600" />
                  )}
                  {isStageActive && (
                    <Loader2 className="h-4 w-4 text-blue-600 animate-spin" />
                  )}
                  {isStageError && (
                    <XCircle className="h-4 w-4 text-red-500" />
                  )}
                  {isPendingStage && (
                    <Icon className="h-4 w-4 text-gray-300" />
                  )}
                </div>

                {/* Linha conectora (exceto no último) */}
                {idx < STAGES.length - 1 && (
                  <div className={cn(
                    'w-0.5 flex-1 my-1 min-h-[16px]',
                    isDone ? 'bg-green-200' : 'bg-gray-100'
                  )} />
                )}
              </div>

              {/* Conteúdo da etapa */}
              <div className={cn(
                'flex-1 pb-4',
                idx === STAGES.length - 1 && 'pb-0'
              )}>
                <div className="flex items-center gap-2 min-h-[32px]">
                  <p className={cn(
                    'text-sm font-medium leading-tight',
                    isDone        ? 'text-gray-700' :
                    isStageActive ? 'text-blue-700' :
                    isStageError  ? 'text-red-600'  :
                    'text-gray-350'
                  )}
                  style={isPendingStage ? { color: '#9ca3af' } : undefined}
                  >
                    {stage.label}
                  </p>

                  {timeLabel && (
                    <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded font-medium">
                      {timeLabel}
                    </span>
                  )}

                  {isDone && total_products != null && stage.status === 'PARSING' && (
                    <span className="text-xs text-green-600">
                      {total_products} produtos
                    </span>
                  )}
                </div>

                <p className={cn(
                  'text-xs mt-0.5 leading-relaxed',
                  isDone        ? 'text-gray-400' :
                  isStageActive ? 'text-blue-600' :
                  isStageError  ? 'text-red-500'  :
                  'text-gray-300'
                )}>
                  {isStageActive ? stage.activeDetail : stage.description}
                </p>

                {/* Barra de progresso intra-etapa: só para RESEARCHING com dados */}
                {isStageActive && stage.status === 'RESEARCHING' && total_products != null && total_products > 0 && (
                  <div className="mt-2 space-y-1">
                    <div className="h-1 bg-blue-100 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-blue-400 rounded-full transition-all duration-500"
                        style={{
                          width: `${Math.round(((processed_products ?? 0) / total_products) * 100)}%`,
                          minWidth: processed_products ? undefined : '4%',
                        }}
                      />
                    </div>
                    <p className="text-xs text-blue-400">
                      {processed_products ?? 0} de {total_products} produtos pesquisados
                    </p>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Erro ────────────────────────────────────────────────── */}
      {isError && error_message && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          <span className="font-medium">Detalhe: </span>{error_message}
        </div>
      )}

      {/* ── Rodapé ──────────────────────────────────────────────── */}
      {isActive && (
        <p className="text-xs text-gray-400 text-center">
          Atualizando a cada 5 segundos...
        </p>
      )}
    </div>
  )
}
