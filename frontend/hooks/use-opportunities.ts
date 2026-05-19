'use client'

import { useQuery } from '@tanstack/react-query'
import { getOpportunities } from '@/lib/api'
import type { OpportunityFilters, OpportunityListResponse } from '@/types'

export function useOpportunities(
  catalogId: string,
  filters: OpportunityFilters = {},
  enabled = true,
) {
  return useQuery<OpportunityListResponse>({
    // 'enabled' na queryKey garante que quando isReady muda false→true
    // é tratada como uma query nova, sem erro cacheado do processamento anterior
    queryKey: ['opportunities', catalogId, filters, enabled],
    queryFn: () =>
      getOpportunities(catalogId, {
        min_score: filters.min_score,
        recommendation: filters.recommendation || undefined,
      }),
    staleTime: 30_000,
    enabled: !!catalogId && enabled,
    retry: false,
  })
}
