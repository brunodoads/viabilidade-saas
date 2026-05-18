'use client'

import { useState } from 'react'
import { useCatalogStatus } from '@/hooks/use-catalog-status'
import { useOpportunities } from '@/hooks/use-opportunities'
import { PipelineStatus } from '@/components/pipeline-status'
import { OpportunityTable } from '@/components/opportunity-table'
import { FiltersBar } from '@/components/filters-bar'
import { Card, CardContent } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'
import type { OpportunityFilters } from '@/types'

interface Props {
  params: { id: string }
}

export default function CatalogPage({ params }: Props) {
  const catalogId = params.id
  const [filters, setFilters] = useState<OpportunityFilters>({})

  const { data: status, isLoading: statusLoading } = useCatalogStatus(catalogId)
  const isReady = status?.status === 'READY'

  const {
    data: opportunities,
    isLoading: opLoading,
    error: opError,
  } = useOpportunities(catalogId, filters)

  return (
    <div className="space-y-6">
      {/* Back */}
      <Link
        href="/dashboard"
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700"
      >
        <ArrowLeft className="h-4 w-4" />
        Todos os catálogos
      </Link>

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
        </div>
      )}
    </div>
  )
}
