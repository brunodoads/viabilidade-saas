"""
Mercado Livre API Client — Integração real com autenticação OAuth.

Documentação oficial: https://developers.mercadolivre.com.br

Endpoints:
    POST /oauth/token              — App token (client_credentials)
    GET  /sites/MLB/search?q=...   — Busca de produtos (requer token)

Design MVP:
    - App Token (não exige login de usuário — só APP_ID + CLIENT_SECRET)
    - Cache de token em memória (expira em ~6h, renovamos em ~5h)
    - Throttle via time.sleep entre requests
    - Retry exponencial em 429, 503, timeout
    - sold_quantity vem no resultado da busca (campo presente com token)

Para obter credenciais:
    1. Acesse https://developers.mercadolivre.com.br
    2. Crie uma aplicação
    3. Copie APP_ID e CLIENT_SECRET para o .env

Fase 2:
    - Redis para cache de token e resultados (TTL 1h por query)
    - Adapter pattern ao adicionar Amazon/Shopee
"""

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configurações do cliente ──────────────────────────────────────────────────

BASE_URL = "https://api.mercadolibre.com"
OAUTH_URL = f"{BASE_URL}/oauth/token"
PAGE_SIZE = 50          # Máximo por página da API
MAX_PAGES = 2           # 2 páginas = até 100 anúncios por busca
REQUEST_TIMEOUT = 20.0  # Segundos — ML pode ser lento em horários de pico

# Retry exponencial: 1s, 2s, 4s
RETRY_DELAYS = [1.0, 2.0, 4.0]

# ── Cache de token em memória ─────────────────────────────────────────────────
# Simples e suficiente para MVP. Fase 2 usa Redis com TTL.

_token_cache: dict[str, Any] = {
    "access_token": None,
    "expires_at": 0.0,  # Unix timestamp
}

_TOKEN_REFRESH_BUFFER = 300  # Renovar 5 min antes de expirar


# ── Dataclasses de resultado ──────────────────────────────────────────────────

@dataclass
class MLListing:
    """Anúncio bruto do Mercado Livre — dados relevantes para análise."""

    item_id: str
    title: str
    price: Decimal
    sold_quantity: int
    seller_id: int
    condition: str          # "new" | "used"
    listing_type: str       # "gold_special", "gold_pro", etc.
    category_id: str
    permalink: str
    thumbnail: str = ""
    # Reputação do seller (disponível em chamada separada — Fase 2)
    seller_reputation: str = ""
    # Frete e taxa real -- enriquecidos via ML Items API + Listing Prices API
    free_shipping: bool = False
    logistic_type: str = ""
    ml_fee_pct: "Decimal | None" = None


@dataclass
class MLSearchResult:
    """Resultado consolidado de uma busca no ML."""

    query: str
    total_found: int = 0
    listings: list[MLListing] = field(default_factory=list)
    # Logs de diagnóstico
    pages_fetched: int = 0
    api_errors: list[str] = field(default_factory=list)
    used_fallback_query: bool = False


# ── Autenticação OAuth ────────────────────────────────────────────────────────

def get_app_token(app_id: str, client_secret: str) -> str | None:
    """
    Obtém (ou reutiliza cache de) App Token do ML.

    O App Token não exige login de usuário — usa apenas credenciais do app.
    Fluxo: client_credentials (OAuth 2.0)
    Validade: ~6 horas. Renovamos quando restar < 5 min.

    Returns:
        access_token ou None se falhar (ou sem credenciais configuradas)
    """
    if not app_id or not client_secret:
        logger.debug("ML: sem credenciais configuradas — operando sem autenticação")
        return None

    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - _TOKEN_REFRESH_BUFFER:
        return _token_cache["access_token"]

    logger.info("ML Auth: obtendo novo App Token")

    try:
        response = httpx.post(
            OAUTH_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": client_secret,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()

        token = data["access_token"]
        expires_in = data.get("expires_in", 21600)  # default 6h

        _token_cache["access_token"] = token
        _token_cache["expires_at"] = now + expires_in

        logger.info("ML Auth: token obtido | expira em %dh", expires_in // 3600)
        return token

    except httpx.HTTPStatusError as exc:
        logger.error("ML Auth: falha ao obter token HTTP %d: %s", exc.response.status_code, exc.response.text)
        return None
    except Exception as exc:
        logger.error("ML Auth: erro inesperado: %s", exc)
        return None


# ── Busca principal ───────────────────────────────────────────────────────────

def search_listings(
    query: str,
    access_token: str | None = None,
    min_sales: int = 0,
) -> MLSearchResult:
    """
    Busca produtos no ML e retorna lista de anúncios brutos.

    Faz até 2 páginas (100 resultados). Não filtra por sold_quantity aqui —
    a filtragem é responsabilidade do market_service (com matching inteligente).

    Args:
        query: Termo de busca (nome normalizado do produto)
        access_token: Bearer token OAuth. None = tenta sem autenticação.
        min_sales: Filtro mínimo de vendas aplicado após busca (0 = sem filtro).

    Returns:
        MLSearchResult com listagens brutas
    """
    if not query or not query.strip():
        return MLSearchResult(query=query)

    result = MLSearchResult(query=query)
    headers = _build_headers(access_token)
    url = f"{BASE_URL}/sites/MLB/search"

    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        for page in range(MAX_PAGES):
            offset = page * PAGE_SIZE
            params = {
                "q": query,
                "limit": PAGE_SIZE,
                "offset": offset,
                # Não usar sort=sold_quantity_desc — não é um sort válido da API pública.
                # Buscamos relevância (padrão) e filtramos sold_quantity depois.
            }

            raw_items = _fetch_page(client, url, params, headers, query, result)
            if raw_items is None:
                break  # Erro irrecuperável ou resultado vazio

            items = _parse_listings(raw_items)
            result.listings.extend(items)
            result.pages_fetched += 1

            # Se temos menos resultados que o tamanho da página, não há mais dados
            if len(raw_items) < PAGE_SIZE:
                break

            # Throttle entre páginas
            if page < MAX_PAGES - 1:
                time.sleep(0.3)

    result.total_found = len(result.listings)

    logger.info(
        "ML Search: '%s' → %d anúncios em %d página(s) | erros=%d",
        query, result.total_found, result.pages_fetched, len(result.api_errors)
    )

    return result


def _fetch_page(
    client: httpx.Client,
    url: str,
    params: dict,
    headers: dict,
    query: str,
    result: MLSearchResult,
) -> list[dict] | None:
    """
    Fetcha uma página da API com retry exponencial.

    Retry em:
        - 429: Rate limit — espera o tempo indicado pelo Retry-After
        - 503, 504: Servidor indisponível — espera exponencial
        - Timeout: Problema de rede transitório

    Não retria em:
        - 400: Query inválida (bug nosso)
        - 401, 403: Auth — não adianta retentar
        - 404: Não existe
    """
    last_exc = None

    for attempt, delay in enumerate(RETRY_DELAYS):
        try:
            response = client.get(url, params=params, headers=headers)

            # Rate limit — respeitar Retry-After
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", delay * 2))
                logger.warning(
                    "ML API: rate limit na tentativa %d | aguardando %ds",
                    attempt + 1, retry_after
                )
                time.sleep(retry_after)
                continue

            # Sem autenticação — log e desiste (não adianta retentar)
            if response.status_code == 403:
                msg = "ML API: acesso negado (403). Configure ML_APP_ID e ML_CLIENT_SECRET no .env"
                logger.error(msg)
                result.api_errors.append(msg)
                return None

            # Auth inválida
            if response.status_code == 401:
                msg = "ML API: token inválido ou expirado (401). Renovar credenciais."
                logger.error(msg)
                result.api_errors.append(msg)
                # Invalida cache para próxima chamada obter novo token
                _token_cache["access_token"] = None
                return None

            # Servidor indisponível — tenta novamente
            if response.status_code in (503, 504):
                logger.warning(
                    "ML API: servidor indisponível %d (tentativa %d/%d)",
                    response.status_code, attempt + 1, len(RETRY_DELAYS)
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            data = response.json()
            return data.get("results", [])

        except httpx.TimeoutException as exc:
            last_exc = exc
            logger.warning(
                "ML API: timeout na tentativa %d/%d para '%s'",
                attempt + 1, len(RETRY_DELAYS), query
            )
            time.sleep(delay)

        except httpx.HTTPStatusError as exc:
            logger.error(
                "ML API: HTTP %d para '%s': %s",
                exc.response.status_code, query, exc.response.text[:200]
            )
            result.api_errors.append(f"HTTP {exc.response.status_code}")
            return None  # Não retry em outros erros HTTP

        except Exception as exc:
            last_exc = exc
            logger.error("ML API: erro inesperado (tentativa %d): %s", attempt + 1, exc)
            time.sleep(delay)

    if last_exc:
        result.api_errors.append(f"Falhou após {len(RETRY_DELAYS)} tentativas: {last_exc}")

    return None


# ── Parsing de resultados ─────────────────────────────────────────────────────

def _parse_listings(raw_items: list[dict]) -> list[MLListing]:
    """
    Converte resposta bruta da API em MLListings tipados.

    Campos esperados no response (com token autenticado):
        - id, title, price, sold_quantity, condition
        - listing_type_id, category_id, permalink, thumbnail
        - seller.id, seller.nickname
    """
    listings = []

    for item in raw_items:
        try:
            item_id = item.get("id", "")
            title = item.get("title", "").strip()
            price_raw = item.get("price")
            sold_qty = item.get("sold_quantity", 0)

            # Validações básicas — dados corrompidos ou anúncios sem preço
            if not item_id or not title:
                continue
            if price_raw is None or price_raw <= 0:
                continue

            seller_info = item.get("seller", {})

            listings.append(MLListing(
                item_id=str(item_id),
                title=title,
                price=Decimal(str(price_raw)).quantize(Decimal("0.01")),
                sold_quantity=int(sold_qty or 0),
                seller_id=int(seller_info.get("id", 0)),
                condition=item.get("condition", ""),
                listing_type=item.get("listing_type_id", ""),
                category_id=item.get("category_id", ""),
                permalink=item.get("permalink", ""),
                thumbnail=item.get("thumbnail", ""),
            ))

        except (TypeError, ValueError, KeyError) as exc:
            logger.debug("ML: erro ao parsear item %s: %s", item.get("id"), exc)
            continue

    return listings


# ── Estatísticas de mercado ───────────────────────────────────────────────────

def aggregate_market_data(listings: list[MLListing], matches: list | None = None) -> dict | None:
    """
    Calcula estatísticas de mercado a partir de uma lista de anúncios qualificados.

    Usado pelo market_service após aplicar filtros de sold_quantity e matching.

    Args:
        listings: Anúncios já filtrados (por vendas e por confiança de matching)
        matches:  Lista de MatchResult correspondente — usada para avg_match_confidence.

    Returns:
        Dict com estatísticas ou None se lista vazia
    """
    if not listings:
        return None

    prices = [l.price for l in listings]
    seller_ids = {l.seller_id for l in listings if l.seller_id}
    total_sold = sum(l.sold_quantity for l in listings)
    avg_sold = total_sold / len(listings) if listings else 0

    # Confiança média dos matches aprovados
    avg_confidence = None
    if matches:
        avg_confidence = round(sum(m.score for m in matches) / len(matches), 3)

    # Taxa ML real média
    fees = [l.ml_fee_pct for l in listings if l.ml_fee_pct is not None]
    avg_ml_fee_pct = _round(sum(fees) / len(fees)) if fees else None

    # % anuncios com frete gratis
    free_count = sum(1 for l in listings if l.free_shipping)
    free_shipping_pct = _round(Decimal(str(free_count)) / Decimal(str(len(listings))) * Decimal("100"))

    return {
        "avg_price": _round(sum(prices) / len(prices)),
        "min_price": _round(min(prices)),
        "max_price": _round(max(prices)),
        "total_sellers": len(seller_ids),
        "total_listings_found": len(listings),
        "listings_above_threshold": len(listings),
        "avg_sold_quantity": int(avg_sold),
        "total_sold_quantity": total_sold,
        "avg_match_confidence": avg_confidence,
        "avg_ml_fee_pct": avg_ml_fee_pct,
        "free_shipping_pct": free_shipping_pct,
    }


# ── Utilitários ───────────────────────────────────────────────────────────────

def get_item_details(item_id: str, access_token: str | None = None) -> dict | None:
    """
    Busca detalhes de um item específico via ML Items API.

    Este endpoint NÃO está bloqueado (diferente do /sites/MLB/search).
    Retorna sold_quantity real, condition, listing_type, e outros campos.

    Usado para enriquecer listings obtidos via Apify com sold_quantity.

    Args:
        item_id:      ID do item ML (ex: "MLB4290861023")
        access_token: Token OAuth. None = tenta sem autenticação (funciona para itens públicos).

    Returns:
        Dict com campos do item ou None em caso de erro/item não encontrado.
        Campos úteis: sold_quantity, condition, listing_type_id, seller.id, price
    """
    if not item_id:
        return None

    url = f"{BASE_URL}/items/{item_id}"
    headers = _build_headers(access_token)

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=headers)

            if response.status_code == 404:
                logger.debug("ML Items: item %s não encontrado (404)", item_id)
                return None

            if response.status_code in (401, 403):
                logger.warning(
                    "ML Items: acesso negado para %s (%d) — item pode ser privado",
                    item_id, response.status_code
                )
                return None

            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException:
        logger.warning("ML Items: timeout ao buscar item %s", item_id)
        return None
    except Exception as exc:
        logger.error("ML Items: erro ao buscar item %s: %s", item_id, exc)
        return None



def get_listing_fee(
    price: Decimal,
    category_id: str,
    listing_type_id: str = "gold_special",
) -> "Decimal | None":
    """Taxa real do ML via Listing Prices API (publico, sem auth)."""
    if not category_id or price <= Decimal("0"):
        return None
    url = f"{BASE_URL}/sites/MLB/listing_prices"
    params = {"price": str(price), "category_id": category_id, "listing_type_id": listing_type_id}
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(url, params=params)
            if resp.status_code not in (200,):
                return None
            fee = resp.json().get("sale_fee_percentage")
            return Decimal(str(fee)).quantize(Decimal("0.01")) if fee is not None else None
    except Exception as exc:
        logger.debug("ML Listing Prices: erro categoria '%s': %s", category_id, exc)
        return None

def enrich_listings_with_sold_quantity(
    listings: list["MLListing"],
    access_token: str | None = None,
    max_to_enrich: int = 20,
    delay_seconds: float = 0.2,
) -> list["MLListing"]:
    """
    Enriquece listings com sold_quantity real via ML Items API.

    Chamado depois da filtragem por matching para evitar chamadas
    desnecessárias em listings que serão descartados.

    Args:
        listings:       Lista de MLListings (geralmente já filtrada por matching)
        access_token:   Token OAuth (None = tenta sem auth)
        max_to_enrich:  Máximo de items a enriquecer (evita muitas chamadas)
        delay_seconds:  Pausa entre chamadas à API

    Returns:
        Lista com sold_quantity atualizado onde disponível.
        Listings sem item_id válido ou que falharam permanecem com sold_quantity=0.
    """
    if not listings:
        return listings

    to_enrich = listings[:max_to_enrich]
    enriched_count = 0

    fee_enriched = 0
    for i, listing in enumerate(to_enrich):
        if not listing.item_id:
            continue

        details = get_item_details(listing.item_id, access_token)
        if details:
            listing.sold_quantity = int(details.get("sold_quantity", 0) or 0)
            if not listing.condition:
                listing.condition = details.get("condition", "")
            if not listing.listing_type:
                listing.listing_type = details.get("listing_type_id", "")
            if listing.seller_id == 0:
                seller = details.get("seller", {}) or {}
                listing.seller_id = int(seller.get("id", 0) or 0)
            shipping = details.get("shipping") or {}
            listing.free_shipping = bool(shipping.get("free_shipping", False))
            listing.logistic_type = shipping.get("logistic_type") or ""
            if not listing.category_id:
                listing.category_id = details.get("category_id", "")
            enriched_count += 1

        if listing.category_id and listing.price > Decimal("0"):
            listing.ml_fee_pct = get_listing_fee(listing.price, listing.category_id)
            if listing.ml_fee_pct is not None:
                fee_enriched += 1

        if i < len(to_enrich) - 1:
            time.sleep(delay_seconds)

    logger.info(
        "ML Items: enriquecidos %d/%d | sold_qty=%d | taxa_real=%d",
        len(to_enrich), len(to_enrich), enriched_count, fee_enriched
    )

    return listings


def _build_headers(access_token: str | None) -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": "ViabilidadeSaaS/1.0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))

