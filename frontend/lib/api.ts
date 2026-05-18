import { getToken } from './auth'
import type { CatalogListItem, CatalogStatusResponse, CatalogUploadResponse, OpportunityListResponse } from '@/types'

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> ?? {}),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers })

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body?.detail ?? 'Erro desconhecido')
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

export async function login(email: string, password: string) {
  const body = new URLSearchParams({ username: email, password })
  const res = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: 'Credenciais inválidas' }))
    throw new ApiError(res.status, data?.detail ?? 'Credenciais inválidas')
  }
  return res.json()
}

export async function register(email: string, password: string, full_name: string) {
  return request('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, full_name }),
  })
}

// ─── Catalogs ─────────────────────────────────────────────────────────────────

export async function uploadCatalog(file: File): Promise<CatalogUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  return request<CatalogUploadResponse>('/api/catalogs/upload', { method: 'POST', body: form })
}

export async function listCatalogs(): Promise<CatalogListItem[]> {
  return request<CatalogListItem[]>('/api/catalogs/')
}

export async function getCatalogStatus(catalogId: string): Promise<CatalogStatusResponse> {
  return request<CatalogStatusResponse>(`/api/catalogs/${catalogId}/status`)
}

// ─── Opportunities ────────────────────────────────────────────────────────────

export async function getOpportunities(
  catalogId: string,
  filters: { min_score?: number; recommendation?: string } = {},
): Promise<OpportunityListResponse> {
  const params = new URLSearchParams()
  if (filters.min_score != null) params.set('min_score', String(filters.min_score))
  if (filters.recommendation) params.set('recommendation', filters.recommendation)
  const qs = params.toString()
  return request<OpportunityListResponse>(
    `/api/opportunities/${catalogId}${qs ? `?${qs}` : ''}`
  )
}
