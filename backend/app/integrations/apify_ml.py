"""
Apify ML Client — Busca no Mercado Livre via Apify (contorna bloqueio 403).

Por que Apify e não a ML API direta:
    O endpoint GET /sites/MLB/search do Mercado Livre está bloqueado para
    desenvolvedores sem certificação especial desde 2024. Retorna 403 mesmo
    com tokens válidos via client_credentials e até sem autenticação.
    Múltiplos relatos confirmados em Reclame Aqui (2024-2026).

    O Apify tem um scraper mantido ativamente que contorna esse bloqueio:
    Actor: karamelo/mercadolivre-scraper-brasil-portugues
    Stats: 29K runs, 4.6 estrelas, atualizado frequentemente
    Custo:  ~$1/1000 resultados (MVP com 400 produtos = ~$0.03 total)

Campos retornados pelo actor:
    eTituloProduto  — título do produto
    novoPreco       — preço atual (ex: "173,76" no formato brasileiro)
    precoAnterior   — preço anterior (quando há desconto)
    SKU             — ID do item ML (ex: "MLB4290861023")
    Vendedor        — nome do vendedor (frequentemente vazio na busca)
    produtoCategoryID — categoria ML
    resultadosTotais — total de resultados ("168.820 resultados")
    palavraChave    — keyword usada (essencial no modo batch)
    zProdutoLink    — URL do produto

Campo ausente: sold_quantity — enriquecido separadamente via ML Items API
    (GET /items/{item_id} não está bloqueado — ver mercadolivre.py)

Performance:
    Modo individual: 1 chamada Apify por produto (~20s) → 400 produtos = ~2h
    Modo batch:     N produtos por chamada → 400 produtos / 40 = 10 chamadas = ~3min
    Use search_listings_batch() para catálogos grandes.

Fase 2:
    - Substituir por ML API oficial quando Mercado Livre liberar acesso
    - Redis cache de resultados por query (TTL 1h)
    - Seleção dinâmica de actor por performance
"""

import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configurações ─────────────────────────────────────────────────────────────

APIFY_BASE_URL = "https://api.apify.com/v2"
DEFAULT_ACTOR_ID = "karamelo~mercadolivre-scraper-brasil-portugues"

# Sync endpoint: bloqueia até o actor terminar e retorna os itens diretamente
SYNC_TIMEOUT_SECONDS = 120.0  # Batch com 40 keywords pode levar até 90s
HTTP_TIMEOUT = 125.0           # Um pouco maior que o timeout do actor

REQUEST_DELAY_BETWEEN_SEARCHES = 1.0  # segundos entre buscas (educação ao Apify)

# Batch: quantas keywords por chamada Apify
# 40 = bom trade-off: tempo ~30-60s por batch, actor estável
BATCH_SIZE = 40


# ── Dataclasses (espelham MLListing de mercadolivre.py) ──────────────────────

@dataclass
class ApifyMLListing:
    """
    Anúncio do ML via Apify.

    Espelha MLListing de mercadolivre.py para que market_service
    possa usar ambas as fontes sem refatoração.

    sold_quantity é inicialmente 0 — enriquecido pelo ML Items API depois.
    """
    item_id: str           # "MLB4290861023"
    title: str
    price: Decimal
    sold_quantity: int     # 0 até enriquecimento via ML Items API
    seller_id: int         # 0 — Apify não retorna seller ID na busca
    condition: str         # "" — não disponível na busca
    listing_type: str      # "" — não disponível na busca
    category_id: str       # "MLB23332"
    permalink: str
    thumbnail: str = ""
    seller_reputation: str = ""
    # Campo extra para rastrear posição na busca (proxy de popularidade)
    # Posição 1 = primeiro resultado = mais relevante/vendido no ML
    search_position: int = 0


@dataclass
class ApifyMLSearchResult:
    """Resultado consolidado de uma busca via Apify."""
    query: str
    total_found: int = 0
    listings: list = field(default_factory=list)
    pages_fetched: int = 0
    api_errors: list = field(default_factory=list)
    total_results_str: str = ""   # "168.820 resultados" para log


# ── Busca individual (compatibilidade retroativa) ─────────────────────────────

def search_listings_apify(
    query: str,
    api_token: str,
    actor_id: str = DEFAULT_ACTOR_ID,
    max_pages: int = 1,
) -> ApifyMLSearchResult:
    """
    Busca produtos no ML via Apify scraper (modo individual).

    Para catálogos grandes (>20 produtos), prefira search_listings_batch()
    que agrupa múltiplas keywords numa única chamada Apify (~40x mais rápido).
    """
    if not query or not query.strip():
        return ApifyMLSearchResult(query=query)

    if not api_token:
        result = ApifyMLSearchResult(query=query)
        result.api_errors.append("APIFY_API_TOKEN não configurado")
        return result

    results = search_listings_batch(
        queries=[query],
        api_token=api_token,
        actor_id=actor_id,
        max_pages=max_pages,
    )
    return results.get(query, ApifyMLSearchResult(query=query))


# ── Busca em batch (modo performático) ────────────────────────────────────────

def search_listings_batch(
    queries: list,
    api_token: str,
    actor_id: str = DEFAULT_ACTOR_ID,
    max_pages: int = 1,
) -> dict:
    """
    Busca múltiplos produtos no ML em uma única chamada Apify.

    Performance:
        Modo individual: 1 chamada × N produtos = N × 20s
        Modo batch:      ceil(N/BATCH_SIZE) chamadas = ceil(N/40) × 40s

        Exemplo real:
            403 produtos individual: 403 × 20s = 134 minutos
            403 produtos batch:      11 × 40s  = 7 minutos

    O actor karamelo suporta o parâmetro `keywords` (lista de strings).
    Cada item retornado tem o campo `palavraChave` indicando qual keyword gerou.

    Returns:
        Dict de query → ApifyMLSearchResult
    """
    if not queries:
        return {}

    if not api_token:
        return {q: _error_result(q, "APIFY_API_TOKEN não configurado") for q in queries}

    # Deduplicar e filtrar vazias
    unique_queries = list(dict.fromkeys(q for q in queries if q and q.strip()))
    if not unique_queries:
        return {}

    results = {}

    # Processar em batches de BATCH_SIZE
    batches = [unique_queries[i:i + BATCH_SIZE] for i in range(0, len(unique_queries), BATCH_SIZE)]

    logger.info(
        "Apify ML Batch: %d queries → %d batches de até %d keywords",
        len(unique_queries), len(batches), BATCH_SIZE
    )

    for batch_idx, batch_queries in enumerate(batches):
        batch_results = _fetch_batch(
            queries=batch_queries,
            api_token=api_token,
            actor_id=actor_id,
            max_pages=max_pages,
            batch_idx=batch_idx,
            total_batches=len(batches),
        )
        results.update(batch_results)

        # Delay entre batches (exceto o último)
        if batch_idx < len(batches) - 1:
            time.sleep(REQUEST_DELAY_BETWEEN_SEARCHES)

    # Garantir que toda query original tem uma entrada (mesmo que vazia)
    for q in unique_queries:
        if q not in results:
            results[q] = ApifyMLSearchResult(query=q)

    return results


def _fetch_batch(queries, api_token, actor_id, max_pages, batch_idx, total_batches):
    """Executa uma única chamada Apify com múltiplas keywords."""

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": api_token}
    payload = {"keywords": queries, "maxPages": max_pages}

    logger.info(
        "Apify ML Batch [%d/%d]: %d keywords | ex: %s...",
        batch_idx + 1, total_batches, len(queries), queries[0][:40]
    )

    # Inicializar resultados vazios para este batch
    batch_results = {q: ApifyMLSearchResult(query=q) for q in queries}

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.post(url, params=params, json=payload)

            if response.status_code == 401:
                msg = "Apify: token inválido (401)."
                logger.error(msg)
                for r in batch_results.values():
                    r.api_errors.append(msg)
                return batch_results

            if response.status_code == 402:
                msg = "Apify: conta sem créditos (402). Adicionar créditos em console.apify.com."
                logger.error(msg)
                for r in batch_results.values():
                    r.api_errors.append(msg)
                return batch_results

            if response.status_code == 400:
                logger.warning(
                    "Apify: 400 no batch — actor pode não suportar 'keywords'. "
                    "Tentando fallback com keyword singular para cada query."
                )
                return _fetch_batch_fallback(queries, api_token, actor_id, max_pages)

            if response.status_code not in (200, 201):
                msg = f"Apify: HTTP {response.status_code}"
                logger.error(msg)
                for r in batch_results.values():
                    r.api_errors.append(msg)
                return batch_results

            raw_items = response.json()

            if not isinstance(raw_items, list):
                msg = f"Apify: resposta inesperada (não é lista): {str(raw_items)[:200]}"
                logger.error(msg)
                for r in batch_results.values():
                    r.api_errors.append(msg)
                return batch_results

    except httpx.TimeoutException:
        msg = f"Apify: timeout após {HTTP_TIMEOUT}s (batch de {len(queries)} keywords)"
        logger.error(msg)
        for r in batch_results.values():
            r.api_errors.append(msg)
        return batch_results

    except Exception as exc:
        msg = f"Apify: erro inesperado: {exc}"
        logger.error(msg)
        for r in batch_results.values():
            r.api_errors.append(msg)
        return batch_results

    # Agrupar itens por keyword usando o campo 'palavraChave'
    keyword_items = {q: [] for q in queries}

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        keyword = (item.get("palavraChave") or "").strip()

        if keyword in keyword_items:
            keyword_items[keyword].append(item)
        else:
            match = next((q for q in queries if q.lower() == keyword.lower()), None)
            if match:
                keyword_items[match].append(item)
            else:
                logger.debug("Apify batch: item sem keyword correspondente: '%s'", keyword[:50])

    # Converter para ApifyMLSearchResult por query
    total_items = 0
    for query, items in keyword_items.items():
        listings = _parse_apify_items(items)
        result = batch_results[query]
        result.listings = listings
        result.total_found = len(listings)
        result.pages_fetched = max_pages
        if items:
            result.total_results_str = items[0].get("resultadosTotais", "")
        total_items += len(listings)

    logger.info(
        "Apify ML Batch [%d/%d]: %d keywords → %d listings totais",
        batch_idx + 1, total_batches, len(queries), total_items
    )

    return batch_results


def _fetch_batch_fallback(queries, api_token, actor_id, max_pages):
    """
    Fallback para quando o actor não suporta 'keywords' array.
    Processa cada query individualmente usando o parâmetro 'keyword' singular.
    """
    logger.warning("Apify: usando fallback individual para %d queries", len(queries))
    results = {}

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": api_token}

    for i, query in enumerate(queries):
        result = ApifyMLSearchResult(query=query)
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                response = client.post(url, params=params, json={"keyword": query, "maxPages": max_pages})
                if response.status_code not in (200, 201):
                    result.api_errors.append(f"HTTP {response.status_code}")
                else:
                    raw_items = response.json()
                    if isinstance(raw_items, list):
                        result.listings = _parse_apify_items(raw_items)
                        result.total_found = len(result.listings)
                        result.pages_fetched = max_pages
                        if raw_items:
                            result.total_results_str = raw_items[0].get("resultadosTotais", "")
        except Exception as exc:
            result.api_errors.append(str(exc))

        results[query] = result
        if i < len(queries) - 1:
            time.sleep(0.5)

    return results


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_apify_items(raw_items):
    """
    Converte itens do actor Apify em ApifyMLListings tipados.
    """
    listings = []

    for position, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        try:
            title = (item.get("eTituloProduto") or "").strip()
            sku = (item.get("SKU") or "").strip()
            price_str = (item.get("novoPreco") or "").strip()
            permalink = (item.get("zProdutoLink") or "").strip()
            thumbnail = (item.get("imagemLink") or "").strip()
            category_id = (item.get("produtoCategoryID") or "").strip()

            if not title or not price_str:
                continue

            price = _parse_br_price(price_str)
            if price is None or price <= 0:
                continue

            item_id = sku or _extract_item_id_from_url(permalink)

            listings.append(ApifyMLListing(
                item_id=item_id,
                title=title,
                price=price,
                sold_quantity=0,
                seller_id=0,
                condition="",
                listing_type="",
                category_id=category_id,
                permalink=permalink,
                thumbnail=thumbnail,
                search_position=position + 1,
            ))
        except Exception as exc:
            logger.debug("Apify: erro ao parsear item posição %d: %s", position, exc)
            continue

    return listings


def _parse_br_price(price_str):
    """
    Converte preço no formato brasileiro para Decimal.
    Ex: "173,76" → Decimal("173.76"), "1.234,56" → Decimal("1234.56")
    """
    if not price_str:
        return None

    cleaned = re.sub(r"[R$\s]", "", price_str).strip()
    if not cleaned:
        return None

    try:
        if "," in cleaned:
            normalized = cleaned.replace(".", "").replace(",", ".")
            return Decimal(normalized).quantize(Decimal("0.01"))
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        logger.debug("Apify: não conseguiu parsear preço '%s'", price_str)
        return None


def _extract_item_id_from_url(url):
    """Extrai ID do item ML a partir da URL. Ex: MLB-4290861023 → MLB4290861023"""
    if not url:
        return ""
    match = re.search(r"MLB-?(\d+)", url, re.IGNORECASE)
    if match:
        return f"MLB{match.group(1)}"
    return ""


def _error_result(query, msg):
    r = ApifyMLSearchResult(query=query)
    r.api_errors.append(msg)
    return r


# ── Compatibilidade com mercadolivre.py ──────────────────────────────────────

def to_ml_listings(apify_listings):
    """
    Converte ApifyMLListings para MLListings (de mercadolivre.py).
    Permite que market_service use ambas as fontes sem código duplicado.
    """
    from app.integrations.mercadolivre import MLListing
    return [
        MLListing(
            item_id=a.item_id,
            title=a.title,
            price=a.price,
            sold_quantity=a.sold_quantity,
            seller_id=a.seller_id,
            condition=a.condition,
            listing_type=a.listing_type,
            category_id=a.category_id,
            permalink=a.permalink,
            thumbnail=a.thumbnail,
        )
        for a in apify_listings
    ]
