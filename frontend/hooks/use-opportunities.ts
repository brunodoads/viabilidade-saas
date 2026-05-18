'use client'

import { useQuery } from '@tanstack/react-query'
import { getOpportunities } from '@/lib/api'
import type { OpportunityFilters, OpportunityListResponse } from '@/types'

export function useOpportunities(catalogId: string, filters: OpportunityFilters = {}) {
  return useQuery<OpportunityListResponse>({
    queryKey: ['opportunities', catalogId, filters],
    queryFn: () =>
      getOpportunities(catalogId, {
        min_score: filters.min_score,
        recommendation: filters.recommendation || undefined,
      }),
    staleTime: 30_000,
    enabled: !!catalogId,
  })
}
