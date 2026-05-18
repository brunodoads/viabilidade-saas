export type CatalogStatus = 'PENDING' | 'PROCESSING' | 'READY' | 'ERROR'
export type FileType = 'XLSX' | 'CSV' | 'PDF'
export type Recommendation = 'EXCELENTE' | 'BOA' | 'ARRISCADA' | 'EVITAR'

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface CatalogUploadResponse {
  id: string
  original_filename: string
  file_type: FileType
  status: CatalogStatus
  created_at: string
}

export interface CatalogStatusResponse {
  id: string
  original_filename: string
  status: CatalogStatus
  error_message: string | null
  total_products: number | null
  processed_products: number | null
  progress_pct: number | null
  created_at: string
  updated_at: string
}

export interface CatalogListItem {
  id: string
  original_filename: string
  file_type: FileType
  status: CatalogStatus
  total_products: number | null
  created_at: string
}

export interface MarketData {
  avg_price: number
  min_price: number
  max_price: number
  total_sellers: number
  listings_above_threshold: number
}

export interface FinancialData {
  cost: number
  avg_market_price: number
  marketplace_fee_pct: number
  gross_margin: number
  gross_margin_pct: number
  break_even_price: number
  is_viable: boolean
}

export interface OpportunityItem {
  product_id: string
  raw_name: string
  normalized_name: string | null
  sku: string | null
  category: string | null
  cost: number
  final_score: number
  rank: number
  recommendation: Recommendation
  demand_score: number
  margin_score: number
  competition_score: number
  market: MarketData | null
  financial: FinancialData | null
}

export interface OpportunityListResponse {
  catalog_id: string
  total: number
  items: OpportunityItem[]
}

export interface OpportunityFilters {
  min_score?: number
  recommendation?: Recommendation | ''
}
