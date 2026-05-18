'use client'

import { useQuery } from '@tanstack/react-query'
import { listCatalogs } from '@/lib/api'
import { CatalogListItem } from '@/types'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'
import { Button } from '@/components/ui/button'
import { formatDate } from '@/lib/utils'
import { FileSpreadsheet, FilePlus, ChevronRight } from 'lucide-react'
import Link from 'next/link'

type BadgeVariant = 'gray' | 'info' | 'success' | 'danger'

const STATUS_CONFIG: Record<string, { label: string; variant: BadgeVariant }> = {
  PENDING:     { label: 'Na fila',         variant: 'gray' },
  PARSING:     { label: 'Lendo arquivo',   variant: 'info' },
  RESEARCHING: { label: 'Pesquisando ML',  variant: 'info' },
  ANALYZING:   { label: 'Calculando',      variant: 'info' },
  SCORING:     { label: 'Pontuando',       variant: 'info' },
  READY:       { label: 'Pronto',          variant: 'success' },
  ERROR:       { label: 'Erro',            variant: 'danger' },
}

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery<CatalogListItem[]>({
    queryKey: ['catalogs'],
    queryFn: () => listCatalogs() as Promise<CatalogListItem[]>,
    refetchInterval: 10_000,
  })

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Catálogos</h1>
          <p className="text-sm text-gray-500 mt-0.5">Análises de viabilidade dos seus catálogos</p>
        </div>
        <Link href="/upload">
          <Button>
            <FilePlus className="h-4 w-4" />
            Novo catálogo
          </Button>
        </Link>
      </div>

      {/* Content */}
      {isLoading && (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      )}

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          Erro ao carregar catálogos. Verifique sua conexão.
        </div>
      )}

      {data && data.length === 0 && (
        <div className="text-center py-16 space-y-3">
          <FileSpreadsheet className="mx-auto h-12 w-12 text-gray-300" />
          <p className="text-gray-500 font-medium">Nenhum catálogo ainda</p>
          <p className="text-sm text-gray-400">Envie seu primeiro catálogo para começar a análise</p>
          <Link href="/upload">
            <Button className="mt-2">Enviar catálogo</Button>
          </Link>
        </div>
      )}

      {data && data.length > 0 && (
        <div className="space-y-3">
          {data.map((catalog) => {
            const cfg = STATUS_CONFIG[catalog.status] ?? { label: catalog.status, variant: 'gray' as BadgeVariant }
            return (
              <Link key={catalog.id} href={`/catalogs/${catalog.id}`}>
                <Card className="hover:border-blue-200 hover:shadow transition-all cursor-pointer">
                  <CardContent className="flex items-center gap-4 py-4">
                    <FileSpreadsheet className="h-8 w-8 text-green-600 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-gray-800 truncate">{catalog.original_filename}</p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {formatDate(catalog.created_at)}
                        {catalog.total_products != null && ` · ${catalog.total_products} produtos`}
                      </p>
                    </div>
                    <Badge variant={cfg.variant}>{cfg.label}</Badge>
                    <ChevronRight className="h-4 w-4 text-gray-300 flex-shrink-0" />
                  </CardContent>
                </Card>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
