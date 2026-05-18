'use client'

import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { ScoreBadge } from '@/components/score-badge'
import { Spinner } from '@/components/ui/spinner'
import { formatCurrency, formatPercent } from '@/lib/utils'
import type { OpportunityItem } from '@/types'

interface OpportunityTableProps {
  items: OpportunityItem[]
  loading?: boolean
  error?: string | null
}

export function OpportunityTable({ items, loading, error }: OpportunityTableProps) {
  if (loading) {
    return (
      <div className="flex justify-center items-center py-16">
        <Spinner className="h-8 w-8" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="py-16 text-center text-red-600 text-sm">{error}</div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="py-16 text-center text-gray-400 text-sm">
        Nenhuma oportunidade encontrada com os filtros selecionados.
      </div>
    )
  }

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">#</TableHead>
            <TableHead>Produto</TableHead>
            <TableHead>SKU</TableHead>
            <TableHead className="text-right">Custo</TableHead>
            <TableHead className="text-right">Preço ML</TableHead>
            <TableHead className="text-right">Margem</TableHead>
            <TableHead className="text-right">Score</TableHead>
            <TableHead>Classificação</TableHead>
            <TableHead className="text-right">Vendedores</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const margin = item.financial?.gross_margin_pct
            const avgPrice = item.financial?.avg_market_price ?? item.market?.avg_price
            const marginColor =
              margin == null
                ? 'text-gray-400'
                : Number(margin) >= 20
                ? 'text-green-600 font-medium'
                : Number(margin) >= 10
                ? 'text-amber-600'
                : 'text-red-600'

            return (
              <TableRow key={item.product_id}>
                <TableCell className="text-gray-400 text-xs">{item.rank}</TableCell>
                <TableCell>
                  <div>
                    <p className="font-medium text-gray-800 text-sm leading-tight">
                      {item.normalized_name ?? item.raw_name}
                    </p>
                    {item.category && (
                      <p className="text-xs text-gray-400 mt-0.5">{item.category}</p>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-gray-400 text-xs">{item.sku ?? '—'}</TableCell>
                <TableCell className="text-right text-sm">
                  {formatCurrency(Number(item.cost))}
                </TableCell>
                <TableCell className="text-right text-sm">
                  {avgPrice != null ? formatCurrency(Number(avgPrice)) : '—'}
                </TableCell>
                <TableCell className={`text-right text-sm ${marginColor}`}>
                  {margin != null ? formatPercent(Number(margin)) : '—'}
                </TableCell>
                <TableCell className="text-right">
                  <span className="text-sm font-semibold text-gray-700">
                    {Number(item.final_score).toFixed(0)}
                  </span>
                </TableCell>
                <TableCell>
                  <ScoreBadge recommendation={item.recommendation} />
                </TableCell>
                <TableCell className="text-right text-sm text-gray-500">
                  {item.market?.total_sellers ?? '—'}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
