"""
ml_scraper.py — Scraper do site Mercado Livre Brasil

Alternativa gratuita ao cliente API oficial (/sites/MLB/search requer certificação).
Estratégia em 2 etapas:
  1. Raspa lista.mercadolivre.com.br com headers de browser → lista de item_ids
  2. Enriquece via API pública /items/{id} (não requer autenticação) → sold_quantity real

Retorna MLSearchResult com MLListing — compatível com o restante do pipeline.

Limitações vs API oficial:
  - Frágil se ML mudar layout (usa classes do Andes Design System que são estáveis)
  - Sem paginação eficiente (≈50 itens por busca)
  - Mais lento (~1,5s por produto na busca + 0,5s por enriquecimento)

Vantagens:
  - Gratuito, sem certificação ML
  - Funciona de IP residencial
  - /items/{id} é API pública estável (não muda)
"""

from __future__ import annotations

import logging
import re
import time
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from app.integrations.mercadolivre import MLListing, MLSearchResult

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

SEARCH_BASE = "https://lista.mercadolivre.com.br"
ITEMS_API   = "https://api.mercadolibre.com/items"

SEARCH_DELAY  = 1.5   # segundos entre buscas (evita ban)
ENRICH_DELAY  = 0.4   # segundos entre chamadas /items/{id}
MAX_ENRICH    = 15    # máximo de itens a enriquecer por busca
SEARCH_TIMEOUT = 15   # timeout da página de busca
ENRICH_TIMEOUT = 8    # timeout do /items/{id}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _query_to_slug(query: str) -> str:
    """'Teclado Gamer RGB' → 'teclado-gamer-rgb'"""
    slug = query.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug


def _extract_item_id(url: str) -> str | None:
    """Extrai 'MLB1234567890' de qualquer URL do ML."""
    m = re.search(r"MLB-?(\d+)", url, re.IGNORECASE)
    return f"MLB{m.group(1)}" if m else None


def _parse_price(text: str) -> Decimal:
    """'1.299' ou '1299' (formato BR) → Decimal('1299.00')"""
    try:
        cleaned = re.sub(r"[R$\s]", "", text)
        # Ponto como separador de milhar; vírgula como decimal
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(".", "")
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def _parse_sold(text: str) -> int:
    """'1.245 vendidos' ou '+1mil' → int"""
    text = text.lower().replace(".", "").replace(",", "")
    # '+1mil vendidos' → 1000
    mil = re.search(r"(\d+)\s*mil", text)
    if mil:
        return int(mil.group(1)) * 1000
    n = re.search(r"(\d+)", text)
    return int(n.group(1)) if n else 0


# ── Scraping ──────────────────────────────────────────────────────────────────

def _scrape_search_page(query: str, client: httpx.Client) -> list[dict]:
    """
    Busca no site ML e extrai cards de produto da página de resultados.
    Retorna lista de dicts com: item_id, title, price, permalink, sold_quantity (se visível).
    """
    slug = _query_to_slug(query)
    url  = f"{SEARCH_BASE}/{slug}"

    try:
        resp = client.get(url, timeout=SEARCH_TIMEOUT)
    except httpx.TimeoutException:
        logger.warning("Scraper: timeout '%s'", query)
        return []
    except Exception as exc:
        logger.error("Scraper: erro ao buscar '%s': %s", query, exc)
        return []

    if resp.status_code != 200:
        logger.warning("Scraper: HTTP %d para '%s'", resp.status_code, query)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Seletores compatíveis com layout novo (Polaris/Andes) e legado
    cards = (
        soup.select("li.ui-search-layout__item")
        or soup.select(".andes-card.ui-search-result")
        or soup.select(".ui-search-result__wrapper")
    )

    if not cards:
        logger.info("Scraper: '%s' → nenhum card encontrado (layout desconhecido)", query)
        return []

    products: list[dict] = []
    for card in cards:
        # ── Link & ID ──
        link_el = (
            card.select_one("a.poly-component__title")
            or card.select_one("a.ui-search-link")
            or card.select_one("a[href*='mercadolivre']")
        )
        if not link_el:
            continue
        permalink = link_el.get("href", "")
        item_id   = _extract_item_id(permalink)
        if not item_id:
            continue

        # ── Título ──
        title_el = (
            card.select_one(".poly-component__title")
            or card.select_one(".ui-search-item__title")
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # ── Preço ──
        price_el = (
            card.select_one(".andes-money-amount__fraction")
            or card.select_one(".price-tag-fraction")
        )
        price = _parse_price(price_el.get_text(strip=True)) if price_el else Decimal("0")

        # ── Vendas (opcional — aparece em itens populares) ──
        sold_el = (
            card.select_one(".poly-component__sold-title")
            or card.select_one(".ui-search-reviews__sold")
            or card.select_one("[class*='sold']")
        )
        sold_qty = _parse_sold(sold_el.get_text(strip=True)) if sold_el else 0

        products.append({
            "item_id":      item_id,
            "title":        title,
            "price":        price,
            "permalink":    permalink,
            "sold_quantity": sold_qty,
        })

    logger.info("Scraper: '%s' → %d cards parseados", query, len(products))
    return products


# ── Enriquecimento via API pública ────────────────────────────────────────────

def _enrich_item(item_id: str, client: httpx.Client) -> dict:
    """
    GET /items/{id} — endpoint público, sem autenticação.
    Retorna sold_quantity, condition, seller_id, free_shipping, listing_type_id, category_id.
    """
    try:
        resp = client.get(f"{ITEMS_API}/{item_id}", timeout=ENRICH_TIMEOUT)
        if resp.status_code == 200:
            d = resp.json()
            shipping = d.get("shipping") or {}
            return {
                "sold_quantity":   d.get("sold_quantity", 0),
                "condition":       d.get("condition", "new"),
                "seller_id":       d.get("seller_id", 0),
                "listing_type":    d.get("listing_type_id", ""),
                "category_id":     d.get("category_id", ""),
                "free_shipping":   shipping.get("free_shipping", False),
                "logistic_type":   shipping.get("logistic_type", ""),
                "price":           Decimal(str(d.get("price", 0))),
            }
    except Exception as exc:
        logger.debug("Enrichment erro %s: %s", item_id, exc)
    return {}


# ── Interface pública ─────────────────────────────────────────────────────────

def search_listings_scraper(
    query: str,
    min_sales: int = 0,
) -> MLSearchResult:
    """
    Busca no site ML + enriquece via API pública.
    Drop-in replacement para mercadolivre.search_listings().

    Args:
        query:     Termo de busca (nome normalizado do produto)
        min_sales: Filtro mínimo de vendas (aplicado após enriquecimento)

    Returns:
        MLSearchResult com listings ordenados por sold_quantity desc
    """
    result = MLSearchResult(query=query)

    with (
        httpx.Client(headers=BROWSER_HEADERS, timeout=SEARCH_TIMEOUT, follow_redirects=True) as browser,
        httpx.Client(timeout=ENRICH_TIMEOUT, follow_redirects=True) as api,
    ):
        # ── Etapa 1: scraping ──
        raw = _scrape_search_page(query, browser)
        if not raw:
            result.api_errors.append("Nenhum resultado no site ML")
            return result

        # ── Etapa 2: enriquecer top N com /items/{id} ──
        for item in raw[:MAX_ENRICH]:
            time.sleep(ENRICH_DELAY)
            enriched = _enrich_item(item["item_id"], api)
            item.update(enriched)

        # ── Etapa 3: converter para MLListing ──
        for item in raw:
            sold = item.get("sold_quantity", 0)
            if min_sales > 0 and sold < min_sales:
                continue

            listing = MLListing(
                item_id      = item["item_id"],
                title        = item["title"],
                price        = item.get("price", Decimal("0")),
                sold_quantity = sold,
                seller_id    = item.get("seller_id", 0),
                condition    = item.get("condition", "new"),
                listing_type = item.get("listing_type", ""),
                category_id  = item.get("category_id", ""),
                permalink    = item["permalink"],
                free_shipping = item.get("free_shipping", False),
                logistic_type = item.get("logistic_type", ""),
            )
            result.listings.append(listing)

        result.total_found   = len(result.listings)
        result.pages_fetched = 1

    result.listings.sort(key=lambda x: x.sold_quantity, reverse=True)
    logger.info(
        "Scraper: '%s' → %d listings (sold_qty filtro=%d)",
        query, result.total_found, min_sales,
    )
    return result
