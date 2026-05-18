'use client'

import { useQuery } from '@tanstack/react-query'
import { getCatalogStatus } from '@/lib/api'
import type { CatalogStatusResponse } from '@/types'

const TERMINAL_STATUSES = new Set(['READY', 'ERROR'])

export function useCatalogStatus(catalogId: string) {
  return useQuery<CatalogStatusResponse>({
    queryKey: ['catalog-status', catalogId],
    queryFn: () => getCatalogStatus(catalogId),
    refetchInterval: (query) =>
      TERMINAL_STATUSES.has(query.state.data?.status ?? '') ? false : 5000,
    staleTime: 0,
  })
}
