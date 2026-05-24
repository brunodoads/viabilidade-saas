'use client'

import { useState } from 'react'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { ScoreBadge } from '@/components/score-badge'
import { Spinner } from '@/components/ui/spinner'
import { formatCurrency, formatPercent } from '@/lib/utils'
import { ChevronDown, ChevronRight, ExternalLink, Truck, TruckIcon } from 'lucide-react'
import type { OpportunityItem, MarketListing } from '@/types'

interface OpportunityTableProps {
  items: OpportunityItem[]
  loading?: boolean
  error?: string | null
}

function LogisticBadge({ type }: { type: string | null }) {
  if (!type) return null
  const labels: Record<string, { label: string; color: string }> = {
    fulfillment: { label: 'Full', color: 'bg-yellow-100 text-yellow-700' },
    drop_off: { label: 'Flex', color: 'bg-blue-100 text-blue-700' },
    self_service: { label: 'Proprio', color: 'bg-gray-100 text-gray-600' },
    xd_drop_off: { label: 'XD', color: 'bg-purple-100 text-purple-700' },
  }
  const info = labels[type] ?? { label: type, color: 'bg-gray-100 text-gray-500' }
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${info.color}`}>
      {info.label}
    </span>
  )
}

function ListingsPanel({ listings }: { listings: MarketListing[] }) {
  if (!listings || listings.length === 0) {
    return (
      <div className="px-4 py-3 text-xs text-gray-400 italic">
        Nenhum anuncio capturado para este produto.
      </div>
    )
  }

  return (
    <div className="px-4 pb-4 pt-2 space-y-2">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Top anuncios no Mercado Livre
      </p>
      <div className="grid gap-2">
        {listings.slice(0, 5).map((lt) => (
          <div
            key={lt.item_id}
            className="flex items-center gap-3 bg-white border border-gray-100 rounded-lg px-3 py-2 hover:border-blue-200 transition-colors"
          >
            {/* Thumbnail */}
            <div className="w-10 h-10 flex-shrink-0 rounded overflow-hidden bg-gray-50 border border-gray-100">
              {lt.thumbnail ? (
                <img
                  src={lt.thumbnail}
                  alt={lt.title}
                  className="w-full h-full object-contain"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-gray-300 text-xs">
                  ML
                </div>
              )}
            </div>

            {/* Info */}
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-gray-800 truncate leading-tight">
                {lt.title}
              </p>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="text-xs text-gray-500">
                  {lt.sold_quantity != null ? lt.sold_quantity.toLocaleString('pt-BR') + ' vendas' : 'vendas n/d'}
                </span>
                {lt.free_shipping && (
                  <span className="inline-flex items-center gap-0.5 text-[10px] text-green-600 font-medium">
                    <Truck className="h-2.5 w-2.5" />
                    Frete gratis
                  </span>
                )}
                <LogisticBadge type={lt.logistic_type} />
                {lt.ml_fee_pct != null && (
                  <span className="text-[10px] text-gray-400">
                    Taxa {Number(lt.ml_fee_pct).toFixed(1)}%
                  </span>
                )}
              </div>
            </div>

            {/* Price + link */}
            <div className="flex-shrink-0 text-right">
              <p className="text-sm font-semibold text-gray-800">
                {formatCurrency(Number(lt.price))}
              </p>
              <a
                href={lt.permalink}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 text-[10px] text-blue-500 hover:text-blue-700 mt-0.5"
              >
                Ver no ML
                <ExternalLink className="h-2.5 w-2.5" />
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function OpportunityTable({ items, loading, error }: OpportunityTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

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

  function toggleExpand(id: string) {
    setExpandedId((prev) => (prev === id ? null : id))
  }

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8"></TableHead>
            <TableHead className="w-6">#</TableHead>
            <TableHead>Produto</TableHead>
            <TableHead>SKU</TableHead>
            <TableHead className="text-right">Custo</TableHead>
            <TableHead className="text-right">
              <span className="flex flex-col items-end leading-tight">
                <span>Preco Competitivo</span>
                <span className="text-[10px] font-normal text-gray-400">P25 / mediana / P75</span>
              </span>
            </TableHead>
            <TableHead className="text-right">Margem</TableHead>
            <TableHead className="text-right">Taxa ML</TableHead>
            <TableHead className="text-right">Score</TableHead>
            <TableHead>Classificacao</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const margin = item.financial?.gross_margin_pct
            const avgPrice = item.financial?.avg_market_price ?? item.market?.avg_price
            const minPrice = item.market?.min_price
            const maxPrice = item.market?.max_price
            const mlFee = item.market?.avg_ml_fee_pct
            const freeShippingPct = item.market?.free_shipping_pct
            const listings = item.market?.listings ?? []
            const isExpanded = expandedId === item.product_id

            const marginColor =
              margin == null
                ? 'text-gray-400'
                : Number(margin) >= 20
                ? 'text-green-600 font-medium'
                : Number(margin) >= 10
                ? 'text-amber-600'
                : 'text-red-600'

            return (
              <>
                <TableRow
                  key={item.product_id}
                  className="cursor-pointer hover:bg-gray-50"
                  onClick={() => toggleExpand(item.product_id)}
                >
                  {/* Expand toggle */}
                  <TableCell className="text-gray-400 pl-3">
                    {isExpanded
                      ? <ChevronDown className="h-3.5 w-3.5" />
                      : <ChevronRight className="h-3.5 w-3.5" />
                    }
                  </TableCell>

                  <TableCell className="text-gray-400 text-xs">{item.rank}</TableCell>

                  <TableCell>
                    <div>
                      <p className="font-medium text-gray-800 text-sm leading-tight">
                        {item.normalized_name ?? item.raw_name}
                      </p>
                      <div className="flex items-center gap-2 mt-0.5">
                        {item.category && (
                          <span className="text-xs text-gray-400">{item.category}</span>
                        )}
                        {freeShippingPct != null && Number(freeShippingPct) > 50 && (
                          <span className="inline-flex items-center gap-0.5 text-[10px] text-green-600 font-medium">
                            <Truck className="h-2.5 w-2.5" />
                            {Number(freeShippingPct).toFixed(0)}% frete gratis
                          </span>
                        )}
                      </div>
                    </div>
                  </TableCell>

                  <TableCell className="text-gray-400 text-xs">{item.sku ?? '—'}</TableCell>

                  <TableCell className="text-right text-sm">
                    {formatCurrency(Number(item.cost))}
                  </TableCell>

                  {/* Price range */}
                  <TableCell className="text-right">
                    <div className="flex flex-col items-end leading-tight">
                      <span className="text-sm font-medium text-gray-800">
                        {avgPrice != null ? formatCurrency(Number(avgPrice)) : '—'}
              3          </span>
                      {minPrice != null && maxPrice != null && (
                        <span className="text-[10px] text-gray-400">
                          {formatCurrency(Number(minPrice))} / {formatCurrency(Number(maxPrice))}
                        </span>
                      )}
                    </div>
                  </TableCell>

                  <TableCell className={`text-right text-sm ${marginColor}`}>
                    {margin != null ? formatPercent(Number(margin)) : '—'}
                  </TableCell>

                  {/* Real ML fee */}
                  <TableCell className="text-right">
                    {mlFee != null ? (
                      <div className="flex flex-col items-end leading-tight">
                        <span className="text-sm text-gray-700">
                          {Number(mlFee).toFixed(1)}%
                        </span>
                        <span className="text-[10px] text-gray-400">real</span>
                      </div>
                    ) : (
                      <span className="text-sm text-gray-300">—</span>
                    )}
                  </TableCell>

                  <TableCell className="text-right">
                    <span className="text-sm font-semibold text-gray-700">
                      {Number(item.final_score).toFixed(0)}
                    </span>
                  </TableCell>

                  <TableCell>
                    <ScoreBadge recommendation={item.recommendation} />
                  </TableCell>
                </TableRow>

                {/* Expanded listings panel */}
                {isExpanded && (
                  <TableRow key={`${item.product_id}-expanded`} className="bg-gray-50">
                    <TableCell colSpan={10} className="p-0">
                      <ListingsPanel listings={listings} />
                    </TableCell>
                  </TableRow>
                )}
              </>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
