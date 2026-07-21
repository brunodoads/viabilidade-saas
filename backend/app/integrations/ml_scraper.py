"""
ml_scraper.py — Scraper do site Mercado Livre Brasil via Playwright

Usa Playwright (browser headless real) para superar o bot-detection do ML
que bloqueia scrapers baseados em httpx/requests com /gz/account-verification.

Estratégia:
  1. Playwright (Chromium headless) visita ML homepage → obtém cookies JS reais
  2. Navega para lista.mercadolivre.com.br → ML verifica sessão via JS → redireciona
     de volta para a busca com resultados reais
  3. Enriquece top itens via API pública /items/{id} com httpx (rápido, sem JS)

Sessão persistente (singleton):
  - Browser + BrowserContext criados UMA VEZ por processo worker
  - Cookies, localStorage e fingerprint mantidos entre todas as buscas
  - Evita re-autenticação a cada produto
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

ITEMS_API      = "https://api.mercadolibre.com/items"
SEARCH_DELAY   = 2.0   # segundos entre produtos (comportamento humano)
ENRICH_DELAY   = 0.4   # segundos entre chamadas /items/{id}
MAX_ENRICH     = 15    # itens a enriquecer por busca
SEARCH_TIMEOUT = 25000  # ms — timeout Playwright por navegação
ENRICH_TIMEOUT = 10     # s — timeout httpx

# ── Singleton Playwright ──────────────────────────────────────────────────────

_pw       = None   # playwright instance
_browser  = None   # chromium browser
_context  = None   # browser context (mantém cookies entre páginas)
_api_sess = None   # httpx session para /items/{id}
_warmed   = False  # se o warm-up já foi feito
_debug_screenshot_saved = False  # salva screenshot/html só 1x para debug


def _get_context():
    """
    Retorna BrowserContext do Playwright.
    Cria browser + context UMA VEZ. Na primeira chamada faz warm-up no ML.
    """
    global _pw, _browser, _context, _api_sess, _warmed

    if _context is None:
        from playwright.sync_api import sync_playwright as _sync_pw
        logger.info("Playwright: iniciando Chromium headless...")
        _pw = _sync_pw().start()
        _browser = _pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        _context = _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            viewport={"width": 1366, "height": 768},
            java_script_enabled=True,
            extra_http_headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        # Stealth patches — escondem sinais de automação do Playwright
        _context.add_init_script("""
            // Remove webdriver flag (o sinal mais óbvio)
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // Simula plugins reais (headless tem 0)
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                ]
            });

            // Simula languages
            Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US', 'en'] });

            // Chrome runtime (ausente em headless)
            if (!window.chrome) {
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
            }

            // Permissions API — headless retorna 'denied', real retorna 'prompt'
            const origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : origQuery(parameters)
            );
        """)
        logger.info("Playwright: Chromium iniciado (stealth patches aplicados)")

    if _api_sess is None:
        _api_sess = httpx.Client(timeout=ENRICH_TIMEOUT, follow_redirects=True)

    if not _warmed:
        _do_warmup()

    return _context


def _do_warmup() -> None:
    """
    Visita ML para estabelecer sessão e cookies.
    Usa www.mercadolivre.com.br que é menos gateado que lista.mercadolivre.com.br.
    """
    global _warmed
    page = _context.new_page()
    try:
        logger.info("Playwright: warm-up — visitando homepage ML...")
        page.goto("https://www.mercadolivre.com.br/", timeout=20000)
        page.wait_for_load_state("networkidle", timeout=15000)
        logger.info("Playwright: homepage OK | URL: %s", page.url[:80])
        time.sleep(2)

        # Seed no lista. para estabelecer cookies antes das buscas reais
        # Com stealth patches, a verificação deve auto-resolver via JS
        logger.info("Playwright: warm-up — seed 'teclado' no lista.mercadolivre...")
        page.goto(
            "https://lista.mercadolivre.com.br/teclado",
            timeout=SEARCH_TIMEOUT,
            wait_until="domcontentloaded",
        )
        time.sleep(2)
        final_url = page.url
        if "account-verification" in final_url:
            logger.info("Playwright: verificação no seed — stealth patches devem resolver, aguardando 20s...")
            try:
                page.wait_for_url("*lista.mercadolivre*", timeout=20000)
                time.sleep(1)
            except Exception:
                logger.warning("Playwright: verificação não auto-resolveu no seed")
            final_url = page.url
        logger.info("Playwright: seed OK | URL: %s", final_url[:80])

    except Exception as exc:
        logger.warning("Playwright: warm-up falhou (%s) — continuando", exc)
    finally:
        page.close()
    _warmed = True


def reset_session() -> None:
    """Fecha browser e recria na próxima chamada (use em caso de ban severo)."""
    global _pw, _browser, _context, _api_sess, _warmed
    for obj in (_context, _browser, _pw):
        if obj:
            try:
                obj.close()
            except Exception:
                pass
    _pw = _browser = _context = None
    _warmed = False
    logger.info("Playwright: sessão descartada — será recriada na próxima busca")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _query_to_slug(query: str) -> str:
    """Converte query para slug URL (para lista.mercadolivre — não usado agora)."""
    slug = query.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug


def _query_to_url(query: str) -> str:
    """URL de busca no lista.mercadolivre.com.br (versão server-rendered do ML Brasil)."""
    slug = _query_to_slug(query)
    return f"https://lista.mercadolivre.com.br/{slug}"


def _extract_item_id(url: str) -> str | None:
    m = re.search(r"MLB-?(\d+)", url, re.IGNORECASE)
    return f"MLB{m.group(1)}" if m else None


def _parse_price(text: str) -> Decimal:
    try:
        cleaned = re.sub(r"[R$\s]", "", text)
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(".", "")
        return Decimal(cleaned)
    except Exception:
        return Decimal("0")


def _parse_sold(text: str) -> int:
    text = text.lower().replace(".", "").replace(",", "")
    mil = re.search(r"(\d+)\s*mil", text)
    if mil:
        return int(mil.group(1)) * 1000
    n = re.search(r"(\d+)", text)
    return int(n.group(1)) if n else 0


# ── Scraping com Playwright ───────────────────────────────────────────────────

def _scrape_search_page(query: str) -> list[dict]:
    """
    Navega para lista.mercadolivre.com.br/{slug} com Playwright.
    Playwright executa JS → passa pela verificação ML → obtém resultados reais.
    """
    context = _get_context()
    url     = _query_to_url(query)
    page    = context.new_page()
    products: list[dict] = []

    try:
        page.goto(url, timeout=SEARCH_TIMEOUT, wait_until="domcontentloaded")
        time.sleep(1.5)  # Aguarda JS inicial

        # Se redirecionou para verificação, aguarda redirect automático
        # Com stealth patches, o JS de verificação do ML deve passar e redirecionar sozinho
        if "account-verification" in page.url:
            logger.info("Playwright: verificação ML — aguardando auto-redirect (25s)...")
            try:
                page.wait_for_url("*lista.mercadolivre*", timeout=25000)
                time.sleep(1.5)
            except Exception:
                logger.warning("Playwright: sem auto-redirect para '%s'", query)

        # Aguarda networkidle para React terminar de renderizar
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # Tenta vários seletores possíveis para os cards de produto
        CARD_SELECTORS = [
            "li.ui-search-layout__item",
            "li[class*='ui-search-layout']",
            ".ui-search-result",
            ".andes-card",
            "section.ui-search-result",
            "[class*='poly-card']",
        ]
        for sel in CARD_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=4000)
                logger.debug("Playwright: seletor '%s' encontrado", sel)
                break
            except Exception:
                pass

        html = page.content()
        current_url = page.url
        logger.info("Playwright: '%s' | URL: %s", query, current_url[:60])

        # Debug: salva screenshot da PRIMEIRA busca (para ver o que o browser vê)
        global _debug_screenshot_saved
        if not _debug_screenshot_saved:
            try:
                import os
                debug_dir = r"C:\Users\bruno\Documents\Claude\Projects\IA de Viabilidade de Produtos"
                shot_path = os.path.join(debug_dir, "debug_ml_screenshot.png")
                html_path = os.path.join(debug_dir, "debug_ml_page.html")
                page.screenshot(path=shot_path, full_page=True)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html[:80000])
                logger.info("Playwright: DEBUG salvo | URL: %s | shot: %s", page.url[:60], shot_path)
                _debug_screenshot_saved = True
            except Exception as dbg_exc:
                logger.warning("Playwright: falha ao salvar debug: %s", dbg_exc)

    except Exception as exc:
        logger.error("Playwright: erro navegando '%s': %s", query, exc)
        page.close()
        return []
    finally:
        try:
            page.close()
        except Exception:
            pass

    # Parse HTML com BeautifulSoup (mesma lógica de antes)
    soup  = BeautifulSoup(html, "html.parser")
    cards = (
        soup.select("li.ui-search-layout__item")
        or soup.select(".andes-card.ui-search-result")
        or soup.select(".ui-search-result__wrapper")
    )

    if not cards:
        logger.info("Playwright: '%s' → nenhum card (0 resultados ou layout desconhecido)", query)
        return []

    for card in cards:
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

        title_el = (
            card.select_one(".poly-component__title")
            or card.select_one(".ui-search-item__title")
        )
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        price_el = (
            card.select_one(".andes-money-amount__fraction")
            or card.select_one(".price-tag-fraction")
        )
        price = _parse_price(price_el.get_text(strip=True)) if price_el else Decimal("0")

        sold_el = (
            card.select_one(".poly-component__sold-title")
            or card.select_one(".ui-search-reviews__sold")
            or card.select_one("[class*='sold']")
        )
        sold_qty = _parse_sold(sold_el.get_text(strip=True)) if sold_el else 0

        products.append({
            "item_id":       item_id,
            "title":         title,
            "price":         price,
            "permalink":     permalink,
            "sold_quantity": sold_qty,
        })

    logger.info("Playwright: '%s' → %d cards", query, len(products))
    return products


# ── Enriquecimento via API pública (httpx) ────────────────────────────────────

def _enrich_item(item_id: str) -> dict:
    """GET /items/{id} — público, sem autenticação. Usa httpx (rápido)."""
    try:
        resp = _api_sess.get(f"{ITEMS_API}/{item_id}", timeout=ENRICH_TIMEOUT)
        if resp.status_code == 200:
            d = resp.json()
            shipping = d.get("shipping") or {}
            return {
                "sold_quantity": d.get("sold_quantity", 0),
                "condition":     d.get("condition", "new"),
                "seller_id":     d.get("seller_id", 0),
                "listing_type":  d.get("listing_type_id", ""),
                "category_id":   d.get("category_id", ""),
                "free_shipping": shipping.get("free_shipping", False),
                "logistic_type": shipping.get("logistic_type", ""),
                "price":         Decimal(str(d.get("price", 0))),
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
    Busca no ML via Playwright + enriquece com /items/{id} via httpx.
    Drop-in replacement para mercadolivre.search_listings().
    """
    result = MLSearchResult(query=query)

    # Garante que _api_sess existe
    _get_context()

    raw = _scrape_search_page(query)
    if not raw:
        result.api_errors.append("Nenhum resultado")
        return result

    for item in raw[:MAX_ENRICH]:
        time.sleep(ENRICH_DELAY)
        enriched = _enrich_item(item["item_id"])
        item.update(enriched)

    for item in raw:
        sold = item.get("sold_quantity", 0)
        if min_sales > 0 and sold < min_sales:
            continue
        listing = MLListing(
            item_id       = item["item_id"],
            title         = item["title"],
            price         = item.get("price", Decimal("0")),
            sold_quantity = sold,
            seller_id     = item.get("seller_id", 0),
            condition     = item.get("condition", "new"),
            listing_type  = item.get("listing_type", ""),
            category_id   = item.get("category_id", ""),
            permalink     = item["permalink"],
            free_shipping = item.get("free_shipping", False),
            logistic_type = item.get("logistic_type", ""),
        )
        result.listings.append(listing)

    result.total_found   = len(result.listings)
    result.pages_fetched = 1
    result.listings.sort(key=lambda x: x.sold_quantity, reverse=True)

    logger.info("Playwright: '%s' → %d listings", query, result.total_found)
    return result
