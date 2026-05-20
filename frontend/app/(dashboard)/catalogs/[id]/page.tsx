'use client'

import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useCatalogStatus } from '@/hooks/use-catalog-status'
import { useOpportunities } from '@/hooks/use-opportunities'
import { PipelineStatus } from '@/components/pipeline-status'
import { OpportunityTable } from '@/components/opportunity-table'
import { FiltersBar } from '@/components/filters-bar'
import { Card, CardContent } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { reprocessCatalog } from '@/lib/api'
import type { OpportunityFilters } from '@/types'

interface Props {
  params: { id: string }
}

export default function CatalogPage({ params }: Props) {
  const catalogId = params.id
  const router = useRouter()
  const [filters, setFilters] = useState<OpportunityFilters>({})
  const [reprocessing, setReprocessing] = useState(false)
  const [reprocessError, setReprocessError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { data: status, isLoading: statusLoading, refetch: refetchStatus } = useCatalogStatus(catalogId)
  const isReady = status?.status === 'READY'
  const isError = status?.status === 'ERROR'
  const canReprocess = isReady || isError

  const {
    data: opportunities,
    isLoading: opLoading,
    error: opError,
  } = useOpportunities(catalogId, filters, isReady)

  const hasZeroOpportunities = isReady && opportunities && opportunities.total === 0 && !opLoading

  async function handleReprocess() {
    setReprocessing(true)
    setReprocessError(null)
    // Limpa cache de oportunidades para garantir fetch fresco quando pipeline terminar
    // Sem isso, um 400 cacheado de uma execução anterior persiste mesmo após READY
    queryClient.removeQueries({ queryKey: ['opportunities', catalogId] })
    try {
      await reprocessCatalog(catalogId)
      // Aguarda um momento e recarrega o status
      setTimeout(() => {
        refetchStatus()
        setReprocessing(false)
      }, 1500)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao re-processar'
      setReprocessError(msg)
      setReprocessing(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Back */}
      <div className="flex items-center justify-between">
        <Link
          href="/dashboard"
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700"
        >
          <ArrowLeft className="h-4 w-4" />
          Todos os catálogos
        </Link>

        {canReprocess && (
          <button
            onClick={handleReprocess}
            disabled={reprocessing}
            className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${reprocessing ? 'animate-spin' : ''}`} />
            {reprocessing ? 'Re-processando...' : 'Re-processar catálogo'}
          </button>
        )}
      </div>

      {reprocessError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {reprocessError}
        </div>
      )}

      {/* Status card */}
      <Card>
        <CardContent className="py-6">
          {statusLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : status ? (
            <PipelineStatus data={status} />
          ) : (
            <p className="text-sm text-gray-500">Catálogo não encontrado.</p>
          )}
        </CardContent>
      </Card>

      {/* Opportunities section — shown only when READY */}
      {isReady && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-gray-900">Oportunidades encontradas</h2>
          </div>

          {hasZeroOpportunities ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-center space-y-3">
              <p className="text-amber-800 font-medium">Nenhuma oportunidade encontrada</p>
              <p className="text-sm text-amber-700 max-w-lg mx-auto">
                O pipeline completou, mas a busca no Mercado Livre não retornou dados suficientes.
                Isso pode ocorrer se as credenciais ML (ML_APP_ID / ML_CLIENT_SECRET) não estiverem
                configuradas — sem elas, o campo <code className="bg-amber-100 px-1 rounded">sold_quantity</code> fica zerado.
              </p>
              <button
                onClick={handleReprocess}
                disabled={reprocessing}
                className="inline-flex items-center gap-2 rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 ${reprocessing ? 'animate-spin' : ''}`} />
                {reprocessing ? 'Re-processando...' : 'Tentar novamente'}
              </button>
            </div>
          ) : (
            <>
              <FiltersBar
                filters={filters}
                onChange={setFilters}
                total={opportunities?.total}
              />

              <OpportunityTable
                items={opportunities?.items ?? []}
                loading={opLoading}
                error={opError ? 'Erro ao carregar oportunidades.' : null}
              />

              {/* Summary stats */}
              {opportunities && opportunities.total > 0 && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {(['EXCELENTE', 'BOA', 'ARRISCADA', 'EVITAR'] as const).map((rec) => {
                    const count = opportunities.items.filter((i) => i.recommendation === rec).length
                    const colors = {
                      EXCELENTE: 'text-green-700 bg-green-50 border-green-100',
                      BOA: 'text-blue-700 bg-blue-50 border-blue-100',
                      ARRISCADA: 'text-amber-700 bg-amber-50 border-amber-100',
                      EVITAR: 'text-red-700 bg-red-50 border-red-100',
                    }
                    return (
                      <div
                        key={rec}
                        className={`border rounded-lg p-3 text-center ${colors[rec]}`}
                      >
                        <p className="text-2xl font-bold">{count}</p>
                        <p className="text-xs font-medium mt-0.5">{rec}</p>
                      </div>
                    )
                  })}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
