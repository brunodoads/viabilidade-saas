"""
Market Service — Pesquisa de mercado no Mercado Livre.

Estratégia de busca (em ordem de prioridade):
    1. Apify BATCH (quando APIFY_API_TOKEN configurado) — MODO PADRÃO
       └─ Agrupa N queries em batches de 40 por chamada Apify
       └─ Performance: 403 produtos em ~7min (vs ~134min no modo individual)
       └─ Após filtragem por matching, enriquece com sold_quantity via ML Items API
    2. ML API direta (fallback — endpoint bloqueado desde 2024)
       └─ Mantido para quando ML liberar acesso oficial
    3. Sem autenticação (último recurso — retorna sold_quantity=0)

Por que batch:
    O /sites/MLB/search está bloqueado por política do ML para developers
    sem certificação. Apify contorna isso. O batch agrupa 40 keywords por
    chamada ao invés de 1, reduzindo de N chamadas para ceil(N/40).

    Fluxo batch:
        1. Coleta todas as queries dos produtos a pesquisar
        2. Dispara search_listings_batch() — ceil(N/40) chamadas Apify
        3. Processa resultado por produto (matching + enriquecimento via Items API)
        4. Segunda rodada batch para produtos sem resultado (query simplificada)

Logs detalhados de cada etapa para diagnóstico em produção.

Fase 2:
    - Redis cache (TTL 1h por query)
    - Múltiplos marketplaces via adapter pattern
    - Seller reputation fetching
"""

import logging
import time

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.product import Product
from app.repositories.opportunity_repo import MarketAnalysisRepository, MarketListingRepository

logger = logging.getLogger(__name__)


def research_catalog(db: Session, products: list[Product], skip_existing: bool = True) -> int:
    """
    Pesquisa mercado para todos os produtos de um catálogo.

    Usa Apify BATCH por padrão: agrupa queries em batches de 40 para
    reduzir de N chamadas Apify para ceil(N/40) chamadas.

    Args:
        skip_existing: Se True, pula produtos que já têm MarketAnalysis no banco.
                       Permite retomar pipeline interrompido sem reprocessar tudo.
                       Padrão True — use False apenas para forçar refresh de preços.

    Returns:
        Número de produtos pesquisados com sucesso (com dados de mercado)
    """
    use_apify = bool(settings.APIFY_API_TOKEN)
    use_ml_api = bool(settings.ML_APP_ID and settings.ML_CLIENT_SECRET)

    if skip_existing:
        already_done = sum(1 for p in products if p.market_analysis is not None)
        if already_done:
            logger.info(
                "Market: pulando %d/%d produtos com MarketAnalysis existente (pipeline resumível)",
                already_done, len(products)
            )

    if use_apify:
        logger.info(
            "Market: usando Apify BATCH para busca ML (contorna bloqueio 403). "
            "Actor: %s", settings.APIFY_ML_ACTOR_ID
        )
        access_token = _try_get_ml_token()
        if access_token:
            logger.info("Market: token ML disponível para enriquecimento sold_quantity")
        else:
            logger.info(
                "Market: sem token ML — sold_quantity enriquecido via /items/{id} sem auth "
                "(funciona para itens públicos)"
            )
        return _research_with_apify_batch(db, products, access_token, skip_existing=skip_existing)

    elif use_ml_api:
        logger.warning(
            "Market: usando ML API direta (endpoint provavelmente bloqueado). "
            "Configure APIFY_API_TOKEN para resultados confiáveis."
        )
        access_token = _try_get_ml_token()
        return _research_with_ml_api(db, products, access_token, skip_existing=skip_existing)

    else:
        logger.warning(
            "Market: nenhuma integração configurada. "
            "Configure APIFY_API_TOKEN (recomendado) ou ML_APP_ID + ML_CLIENT_SECRET. "
            "Buscando sem autenticação — sold_quantity será 0."
        )
        return _research_with_ml_api(db, products, access_token=None, skip_existing=skip_existing)


# ── Estratégia 1: Apify BATCH ─────────────────────────────────────────────────

def _research_with_apify_batch(
    db: Session,
    products: list[Product],
    ml_access_token: str | None,
    skip_existing: bool = True,
) -> int:
    """
    Pesquisa usando Apify BATCH + enriquecimento via ML Items API.

    Performance vs modo individual:
        Individual: 1 chamada Apify × N produtos = N × ~20s
        Batch:      ceil(N/40) chamadas × ~45s   = muito menos

        Exemplo: 403 produtos
            Individual: 403 × 20s = 134 min
            Batch:      11 × 45s  = ~8 min

    Fluxo:
        Fase 1 — Batch principal:
            • Coleta queries de todos os produtos pendentes
            • Executa search_listings_batch() — N/40 chamadas Apify
            • Processa resultado por produto (matching + enriquecimento)

        Fase 2 — Batch de fallback:
            • Produtos sem resultado → query simplificada (3 primeiros tokens)
            • Executa segundo batch só com as queries de fallback
            • Matching com confiança mínima maior (query mais ampla = mais ruído)
    """
    from app.integrations.apify_ml import search_listings_batch
    from app.services.ml_matching import build_search_query

    repo = MarketAnalysisRepository(db)
    listing_repo = MarketListingRepository(db)
    processed = 0

    # Separar produtos que precisam de pesquisa
    products_to_search = []
    for product in products:
        if skip_existing and product.market_analysis is not None:
            processed += 1  # Conta como processado (já tem dados)
            continue
        products_to_search.append(product)

    if not products_to_search:
        logger.info("Market [Apify Batch]: todos os produtos já têm dados")
        return processed

    logger.info(
        "Market [Apify Batch]: %d produtos para pesquisar",
        len(products_to_search)
    )

    # ── Fase 1: Batch principal ───────────────────────────────────────────────

    # Mapear produto → query (deduplicar queries iguais)
    product_queries: dict[str, str] = {
        str(p.id): build_search_query(p.search_name)
        for p in products_to_search
    }
    unique_queries = list(dict.fromkeys(product_queries.values()))  # preserva ordem, sem duplicatas

    logger.info(
        "Market [Apify Batch]: disparando %d queries únicas em batch (FASE 1)...",
        len(unique_queries)
    )

    batch_results = search_listings_batch(
        queries=unique_queries,
        api_token=settings.APIFY_API_TOKEN,
        actor_id=settings.APIFY_ML_ACTOR_ID,
        max_pages=1,
    )

    # Processar resultado de cada produto
    products_needing_fallback = []  # (product, orig_query, simplified)

    for product in products_to_search:
        pid = str(product.id)
        query = product_queries[pid]
        apify_result = batch_results.get(query)

        if apify_result is None or not apify_result.listings:
            if apify_result and apify_result.api_errors:
                logger.warning(
                    "Market [Apify]: '%s' — erros API: %s",
                    query, "; ".join(apify_result.api_errors)
                )
            simplified = _simplify_query(query)
            if simplified != query:
                products_needing_fallback.append((product, query, simplified))
            else:
                logger.info("Market [Apify]: '%s' → 0 resultados, sem fallback possível", query)
            continue

        market_data = _process_apify_result(
            product=product,
            query=query,
            apify_result=apify_result,
            ml_access_token=ml_access_token,
        )

        if market_data:
            _save_market_result(repo, listing_repo, product, market_data)
            processed += 1
        else:
            simplified = _simplify_query(query)
            if simplified != query:
                products_needing_fallback.append((product, query, simplified))

    # ── Fase 2: Batch de fallback (queries simplificadas) ────────────────────

    if products_needing_fallback:
        logger.info(
            "Market [Apify Batch]: FASE 2 — %d produtos com query simplificada",
            len(products_needing_fallback)
        )
        fallback_queries = list(dict.fromkeys(
            simplified for _, _, simplified in products_needing_fallback
        ))
        fallback_batch = search_listings_batch(
            queries=fallback_queries,
            api_token=settings.APIFY_API_TOKEN,
            actor_id=settings.APIFY_ML_ACTOR_ID,
            max_pages=1,
        )
        for product, original_query, simplified_query in products_needing_fallback:
            apify_result = fallback_batch.get(simplified_query)
            if apify_result is None or not apify_result.listings:
                continue
            market_data = _process_apify_result(
                product=product,
                query=simplified_query,
                apify_result=apify_result,
                ml_access_token=ml_access_token,
                is_fallback=True,
                original_search_name=product.search_name,
            )
            if market_data:
                _save_market_result(repo, listing_repo, product, market_data)
                processed += 1

    logger.info(
        "Market [Apify Batch]: concluído | %d/%d produtos com dados de mercado",
        processed, len(products)
    )
    return processed


def _process_apify_result(
    product,
    query: str,
    apify_result,
    ml_access_token,
    is_fallback: bool = False,
    original_search_name = None,
):
    from app.integrations.apify_ml import to_ml_listings
    from app.integrations.mercadolivre import aggregate_market_data, enrich_listings_with_sold_quantity
    from app.services.ml_matching import filter_qualified_listings

    search_name = original_search_name or product.search_name
    if not apify_result or not apify_result.listings:
        return None

    logger.info(
        "Market [Apify]: '%s' → %d listings brutos | total ML: %s%s",
        query, apify_result.total_found, apify_result.total_results_str or "?",
        " [FALLBACK]" if is_fallback else "",
    )
    ml_listings = to_ml_listings(apify_result.listings)
    min_confidence = settings.ML_MIN_MATCH_CONFIDENCE
    if is_fallback:
        min_confidence = min(min_confidence + 0.10, 0.90)

    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_listings,
        min_sales=0,
        min_confidence=min_confidence,
    )
    if not qualified:
        logger.info(
            "Market [Apify]: '%s' → 0 anúncios após matching (confiança >= %.0f%%)%s",
            query, min_confidence * 100, " [FALLBACK]" if is_fallback else "",
        )
        return None

    max_enrich = 10 if is_fallback else 20
    qualified = enrich_listings_with_sold_quantity(
        listings=qualified,
        access_token=ml_access_token,
        max_to_enrich=min(len(qualified), max_enrich),
        delay_seconds=0.15,
    )

    if settings.MIN_SALES_THRESHOLD > 0:
        qualified_filtered = [q for q in qualified if q.sold_quantity >= settings.MIN_SALES_THRESHOLD]
        matches_filtered = [
            m for q, m in zip(qualified, matches)
            if q.sold_quantity >= settings.MIN_SALES_THRESHOLD
        ]
        if qualified_filtered:
            qualified = qualified_filtered
            matches = matches_filtered

    high_count = sum(1 for m in matches if m.tier == "HIGH")
    medium_count = sum(1 for m in matches if m.tier == "MEDIUM")
    avg_conf = sum(m.score for m in matches) / len(matches) if matches else 0
    total_sold = sum(q.sold_quantity for q in qualified)
    logger.info(
        "Market [Apify]: '%s' → %d qualificados | HIGH=%d MEDIUM=%d | confiança=%.0f%% | total_vendas=%d",
        query, len(qualified), high_count, medium_count, avg_conf * 100, total_sold
    )

    result = aggregate_market_data(qualified, matches)
    top_n = 5
    top_listings = []
    for rank, (listing, match) in enumerate(zip(qualified[:top_n], matches[:top_n]), start=1):
        top_listings.append({
            "rank_position": rank,
            "item_id": listing.item_id or "",
            "title": listing.title or "",
            "price": listing.price,
            "sold_quantity": listing.sold_quantity if listing.sold_quantity > 0 else None,
            "permalink": listing.permalink or None,
            "thumbnail": listing.thumbnail or None,
            "match_confidence": round(match.score, 3),
            "free_shipping": listing.free_shipping,
            "logistic_type": listing.logistic_type or None,
            "ml_fee_pct": listing.ml_fee_pct,
            "category_id": listing.category_id or None,
        })
    result["top_listings"] = top_listings
    return result


def _save_market_result(repo, listing_repo, product, market_data):
    repo.upsert(
        product_id=product.id,
        avg_price=market_data["avg_price"],
        min_price=market_data["min_price"],
        max_price=market_data["max_price"],
        total_sellers=market_data["total_sellers"],
        total_listings_found=market_data["total_listings_found"],
        listings_above_threshold=market_data["listings_above_threshold"],
        avg_sold_quantity=market_data.get("avg_sold_quantity"),
        total_sold_quantity=market_data.get("total_sold_quantity"),
        avg_match_confidence=market_data.get("avg_match_confidence"),
        avg_ml_fee_pct=market_data.get("avg_ml_fee_pct"),
        free_shipping_pct=market_data.get("free_shipping_pct"),
    )
    top_listings = market_data.get("top_listings", [])
    if top_listings:
        listing_repo.replace_listings(product_id=product.id, listings=top_listings)
        logger.info("Market [Apify]: '%s' → %d links ML salvos", product.search_name[:40], len(top_listings))


# ── Estratégia 2: ML API direta (fallback) ────────────────────────────────────

def _research_with_ml_api(db, products, access_token, skip_existing=True):
    if access_token:
        logger.info("Market [ML API]: autenticado com OAuth App Token")
    else:
        logger.warning("Market [ML API]: sem token — sold_quantity será 0.")

    effective_min_sales = settings.MIN_SALES_THRESHOLD if access_token else 0
    repo = MarketAnalysisRepository(db)
    processed = 0

    for i, product in enumerate(products):
        if skip_existing and product.market_analysis is not None:
            processed += 1
            continue
        logger.info("Market [ML API]: produto %d/%d | '%s'", i + 1, len(products), product.search_name)
        try:
            market_data = _research_product_ml_api(product=product, access_token=access_token, effective_min_sales=effective_min_sales)
            if market_data is None:
                logger.warning("Market [ML API]: nenhum dado para '%s'", product.search_name)
            else:
                repo.upsert(
                    product_id=product.id,
                    avg_price=market_data["avg_price"],
                    min_price=market_data["min_price"],
                    max_price=market_data["max_price"],
                    total_sellers=market_data["total_sellers"],
                    total_listings_found=market_data["total_listings_found"],
                    listings_above_threshold=market_data["listings_above_threshold"],
                    avg_sold_quantity=market_data.get("avg_sold_quantity"),
                    total_sold_quantity=market_data.get("total_sold_quantity"),
                    avg_match_confidence=market_data.get("avg_match_confidence"),
                )
                processed += 1
        except Exception as exc:
            logger.error("Market [ML API]: erro ao pesquisar '%s': %s", product.search_name, exc, exc_info=True)
        finally:
            if i < len(products) - 1:
                time.sleep(settings.ML_REQUEST_DELAY_SECONDS)

    logger.info("Market [ML API]: concluído | %d/%d produtos com dados", processed, len(products))
    return processed


def _research_product_ml_api(product, access_token, effective_min_sales=0):
    from app.integrations.mercadolivre import aggregate_market_data, search_listings
    from app.services.ml_matching import build_search_query, filter_qualified_listings

    search_name = product.search_name
    query = build_search_query(search_name)
    logger.info("Market [ML API]: query='%s' (original='%s')", query, search_name)
    ml_result = search_listings(query=query, access_token=access_token)

    if ml_result.api_errors:
        logger.warning("Market [ML API]: '%s' — erros: %s", query, "; ".join(ml_result.api_errors))
    if not ml_result.listings:
        logger.info("Market [ML API]: '%s' → 0 anúncios", query)
        return None

    logger.info("Market [ML API]: '%s' → %d anúncios em %d página(s)", query, ml_result.total_found, ml_result.pages_fetched)
    qualified, matches = filter_qualified_listings(
        catalog_name=search_name, listings=ml_result.listings,
        min_sales=effective_min_sales, min_confidence=settings.ML_MIN_MATCH_CONFIDENCE,
    )
    if not qualified:
        simplified = _simplify_query(query)
        if simplified != query:
            return _retry_ml_api_simplified(product=product, simplified_query=simplified, access_token=access_token, search_name=search_name, effective_min_sales=effective_min_sales)
        return None

    return aggregate_market_data(qualified, matches)


def _retry_ml_api_simplified(product, simplified_query, access_token, search_name, effective_min_sales=0):
    from app.integrations.mercadolivre import aggregate_market_data, search_listings
    from app.services.ml_matching import filter_qualified_listings

    logger.info("Market [ML API]: fallback query='%s'", simplified_query)
    ml_result = search_listings(query=simplified_query, access_token=access_token)
    if not ml_result.listings:
        return None
    min_confidence_fallback = min(settings.ML_MIN_MATCH_CONFIDENCE + 0.10, 0.90)
    qualified, matches = filter_qualified_listings(
        catalog_name=search_name, listings=ml_result.listings,
        min_sales=effective_min_sales, min_confidence=min_confidence_fallback,
    )
    if not qualified:
        return None
    return aggregate_market_data(qualified, matches)


# ── Utilitários ────────────────────────────────────────────────────────────────────────────────

def _try_get_ml_token():
    if not settings.ML_APP_ID or not settings.ML_CLIENT_SECRET:
        return None
    from app.integrations.mercadolivre import get_app_token
    return get_app_token(app_id=settings.ML_APP_ID, client_secret=settings.ML_CLIENT_SECRET)


def _simplify_query(query: str) -> str:
    tokens = query.split()
    if len(tokens) <= 3:
        return query
    return " ".join(tokens[:3])
