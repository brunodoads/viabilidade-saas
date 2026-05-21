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
    zProdutoLink    — URL do produto

Campo ausente: sold_quantity — enriquecido separadamente via ML Items API
    (GET /items/{item_id} não está bloqueado — ver mercadolivre.py)

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
# Timeout do actor: geralmente 15-30s por busca com maxPages=1
SYNC_TIMEOUT_SECONDS = 90.0   # Apify pode levar até 60s em pico
HTTP_TIMEOUT = 95.0           # Um pouco maior que o timeout do actor

REQUEST_DELAY_BETWEEN_SEARCHES = 1.0  # segundos entre buscas (educação ao Apify)

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
    listings: list[ApifyMLListing] = field(default_factory=list)
    pages_fetched: int = 0
    api_errors: list[str] = field(default_factory=list)
    total_results_str: str = ""   # "168.820 resultados" para log


# ── Busca principal ───────────────────────────────────────────────────────────

def search_listings_apify(
    query: str,
    api_token: str,
    actor_id: str = DEFAULT_ACTOR_ID,
    max_pages: int = 1,
) -> ApifyMLSearchResult:
    """
    Busca produtos no ML via Apify scraper.

    Usa o endpoint síncrono do Apify: bloqueia até o actor terminar
    e retorna os itens diretamente — sem polling adicional.

    Args:
        query:     Termo de busca (já normalizado pelo ml_matching.build_search_query)
        api_token: Token Apify (de APIFY_API_TOKEN no .env)
        actor_id:  ID do actor (default: karamelo~mercadolivre-scraper-brasil-portugues)
        max_pages: Páginas de resultado a buscar (1 = ~48 resultados)

    Returns:
        ApifyMLSearchResult com listings parseados
    """
    if not query or not query.strip():
        return ApifyMLSearchResult(query=query)

    if not api_token:
        result = ApifyMLSearchResult(query=query)
        result.api_errors.append("APIFY_API_TOKEN não configurado")
        return result

    result = ApifyMLSearchResult(query=query)

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": api_token}
    payload = {
        "keyword": query,
        "maxPages": max_pages,
        # maxPagesOfertas omitido — actor rejeita valor 0, padrão do actor é suficiente
    }

    logger.info("Apify ML: buscando '%s' (actor=%s, maxPages=%d)", query, actor_id, max_pages)

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.post(url, params=params, json=payload)

            if response.status_code == 401:
                msg = "Apify: token inválido (401). Verificar APIFY_API_TOKEN."
                logger.error(msg)
                result.api_errors.append(msg)
                return result

            if response.status_code == 402:
                msg = "Apify: conta sem créditos (402). Adicionar créditos em console.apify.com."
                logger.error(msg)
                result.api_errors.append(msg)
                return result

            if response.status_code == 400:
                msg = f"Apify: request inválido (400): {response.text[:200]}"
                logger.error(msg)
                result.api_errors.append(msg)
                return result

            if response.status_code not in (200, 201):
                msg = f"Apify: HTTP {response.status_code}: {response.text[:200]}"
                logger.error(msg)
                result.api_errors.append(msg)
                return result

            raw_items = response.json()

            if not isinstance(raw_items, list):
                msg = f"Apify: resposta inesperada (não é lista): {str(raw_items)[:200]}"
                logger.error(msg)
                result.api_errors.append(msg)
                return result

    except httpx.TimeoutException:
        msg = f"Apify: timeout após {HTTP_TIMEOUT}s para '{query}'"
        logger.error(msg)
        result.api_errors.append(msg)
        return result

    except Exception as exc:
        msg = f"Apify: erro inesperado: {exc}"
        logger.error(msg)
        result.api_errors.append(msg)
        return result

    # Parsear itens
    listings = _parse_apify_items(raw_items)
    result.listings = listings
    result.total_found = len(listings)
    result.pages_fetched = max_pages

    # Extrair total de resultados do primeiro item (para log)
    if raw_items and isinstance(raw_items[0], dict):
        result.total_results_str = raw_items[0].get("resultadosTotais", "")

    logger.info(
        "Apify ML: '%s' → %d listings | total ML: %s",
        query, result.total_found, result.total_results_str or "desconhecido"
    )

    return result


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_apify_items(raw_items: list[dict]) -> list[ApifyMLListing]:
    """
    Converte itens do actor Apify em ApifyMLListings tipados.

    Campos do actor karamelo:
        eTituloProduto, novoPreco, SKU, Vendedor,
        produtoCategoryID, zProdutoLink, imagemLink, resultadosTotais
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

            # Título e preço são obrigatórios
            if not title or not price_str:
                continue

            # Parse de preço no formato brasileiro ("173,76" → Decimal("173.76"))
            price = _parse_br_price(price_str)
            if price is None or price <= 0:
                continue

            # SKU é o item_id do ML ("MLB4290861023")
            # Se ausente, tentamos extrair do permalink
            item_id = sku or _extract_item_id_from_url(permalink)

            listings.append(ApifyMLListing(
                item_id=item_id,
                title=title,
                price=price,
                sold_quantity=0,    # Enriquecido depois via ML Items API
                seller_id=0,        # Não disponível na busca do Apify
                condition="",       # Não disponível na busca
                listing_type="",    # Não disponível na busca
                category_id=category_id,
                permalink=permalink,
                thumbnail=thumbnail,
                search_position=position + 1,  # 1-indexed
            ))

        except Exception as exc:
            logger.debug("Apify: erro ao parsear item posição %d: %s", position, exc)
            continue

    return listings


def _parse_br_price(price_str: str) -> Decimal | None:
    """
    Converte preço no formato brasileiro para Decimal.

    Formatos suportados:
        "173,76"     → Decimal("173.76")
        "1.234,56"   → Decimal("1234.56")
        "1234.56"    → Decimal("1234.56")  (fallback formato US)
        "R$ 173,76"  → Decimal("173.76")
    """
    if not price_str:
        return None

    # Remover símbolo de moeda e espaços
    cleaned = re.sub(r"[R$\s]", "", price_str).strip()

    if not cleaned:
        return None

    try:
        # Formato brasileiro: "1.234,56" → tem ponto como separador de milhar
        if "," in cleaned:
            # Remove pontos (milhar) e substitui vírgula por ponto decimal
            normalized = cleaned.replace(".", "").replace(",", ".")
            return Decimal(normalized).quantize(Decimal("0.01"))

        # Formato já é ponto decimal: "173.76" ou "1234.56"
        return Decimal(cleaned).quantize(Decimal("0.01"))

    except (InvalidOperation, ValueError):
        logger.debug("Apify: não conseguiu parsear preço '%s'", price_str)
        return None


def _extract_item_id_from_url(url: str) -> str:
    """
    Extrai ID do item ML a partir da URL do produto.

    Ex: "https://produto.mercadolivre.com.br/MLB-4290861023-..." → "MLB4290861023"
    """
    if not url:
        return ""
    match = re.search(r"MLB-?(\d+)", url, re.IGNORECASE)
    if match:
        return f"MLB{match.group(1)}"
    return ""


# ── Compatibilidade com mercadolivre.py ──────────────────────────────────────

def to_ml_listings(apify_listings: list[ApifyMLListing]):
    """
    Converte ApifyMLListings para MLListings (de mercadolivre.py).

    Permite que market_service use ambas as fontes sem código duplicado.
    O sold_quantity começa em 0 e é enriquecido depois.
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
