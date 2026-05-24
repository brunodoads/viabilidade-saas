"""
Market Service â Pesquisa de mercado no Mercado Livre.

EstratÃ©gia de busca (em ordem de prioridade):
    1. Apify BATCH (quando APIFY_API_TOKEN configurado) â MODO PADRÃO
       ââ Agrupa N queries em batches de 40 por chamada Apify
       ââ Performance: 403 produtos em ~7min (vs ~134min no modo individual)
       ââ ApÃ³s filtragem por matching, enriquece com sold_quantity via ML Items API
    2. ML API direta (fallback â endpoint bloqueado desde 2024)
       ââ Mantido para quando ML liberar acesso oficial
    3. Sem autenticaÃ§Ã£o (Ãºltimo recurso â retorna sold_quantity=0)

Por que batch:
    O /sites/MLB/search estÃ¡ bloqueado por polÃ­tica do ML para developers
    sem certificaÃ§Ã£o. Apify contorna isso. O batch agrupa 40 keywords por
    chamada ao invÃ©s de 1, reduzindo de N chamadas para ceil(N/40).

    Fluxo batch:
        1. Coleta todas as queries dos produtos a pesquisar
        2. Dispara search_listings_batch() â ceil(N/40) chamadas Apify
        3. Processa resultado por produto (matching + enriquecimento via Items API)
        4. Segunda rodada batch para produtos sem resultado (query simplificada)

Logs detalhados de cada etapa para diagnÃ³stico em produÃ§Ã£o.

Fase 2:
    - Redis cache (TTL 1h por query)
    - MÃºltiplos marketplaces via adapter pattern
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
    Pesquisa mercado para todos os produtos de um catÃ¡logo.

    Usa Apify BATCH por padrÃ£o: agrupa queries em batches de 40 para
    reduzir de N chamadas Apify para ceil(N/40) chamadas.

    Args:
        skip_existing: Se True, pula produtos que jÃ¡ tÃªm MarketAnalysis no banco.
                       Permite retomar pipeline interrompido sem reprocessar tudo.
                       PadrÃ£o True â use False apenas para forÃ§ar refresh de preÃ§os.

    Returns:
        NÃºmero de produtos pesquisados com sucesso (com dados de mercado)
    """
    use_apify = bool(settings.APIFY_API_TOKEN)
    use_ml_api = bool(settings.ML_APP_ID and settings.ML_CLIENT_SECRET)

    if skip_existing:
        already_done = sum(1 for p in products if p.market_analysis is not None)
        if already_done:
            logger.info(
                "Market: pulando %d/%d produtos com MarketAnalysis existente (pipeline resumÃ­vel)",
                already_done, len(products)
            )

    if use_apify:
        logger.info(
            "Market: usando Apify BATCH para busca ML (contorna bloqueio 403). "
            "Actor: %s", settings.APIFY_ML_ACTOR_ID
        )
        access_token = _try_get_ml_token()
        if access_token:
            logger.info("Market: token ML disponÃ­vel para enriquecimento sold_quantity")
        else:
            logger.info(
                "Market: sem token ML â sold_quantity enriquecido via /items/{id} sem auth "
                "(funciona para itens pÃºblicos)"
            )
        return _research_with_apify_batch(db, products, access_token, skip_existing=skip_existing)

    elif use_ml_api:
        logger.warning(
            "Market: usando ML API direta (endpoint provavelmente bloqueado). "
            "Configure APIFY_API_TOKEN para resultados confiÃ¡veis."
        )
        access_token = _try_get_ml_token()
        return _research_with_ml_api(db, products, access_token, skip_existing=skip_existing)

    else:
        logger.warning(
            "Market: nenhuma integraÃ§Ã£o configurada. "
            "Configure APIFY_API_TOKEN (recomendado) ou ML_APP_ID + ML_CLIENT_SECRET. "
            "Buscando sem autenticaÃ§Ã£o â sold_quantity serÃ¡ 0."
        )
        return _research_with_ml_api(db, products, access_token=None, skip_existing=skip_existing)


# ââ EstratÃ©gia 1: Apify BATCH âââââââââââââââââââââââââââââââââââââââââââââââââ

def _research_with_apify_batch(
    db: Session,
    products: list[Product],
    ml_access_token: str | None,
    skip_existing: bool = True,
) -> int:
    """
    Pesquisa usando Apify BATCH + enriquecimento via ML Items API.

    ARQUITETURA COM CHECKPOINT INCREMENTAL:
        Processa em micro-batches de MICRO_BATCH_SIZE produtos e salva no DB
        apÃ³s cada micro-batch. Isso garante que se o worker for reiniciado,
        apenas o micro-batch em andamento precisa ser refeito.

        Sem checkpoint: 403 produtos Ã 20s = ~150 min de trabalho perdido
        Com checkpoint: mÃ¡ximo 40 produtos Ã 20s = ~14 min de trabalho perdido

    Fluxo por micro-batch (40 produtos):
        1. Coleta queries do micro-batch
        2. Dispara search_listings_batch() â 1 chamada Apify (com fallback individual interno)
        3. Processa matching + enriquecimento para cada produto
        4. Salva no DB (commit) antes de ir para o prÃ³ximo micro-batch
        5. Fase 2 inline: fallback de queries simplificadas no mesmo micro-batch
    """
    from app.integrations.apify_ml import search_listings_batch, BATCH_SIZE
    from app.services.ml_matching import build_search_query

    MICRO_BATCH_SIZE = BATCH_SIZE  # Alinhado com o BATCH_SIZE do apify_ml (40)

    repo = MarketAnalysisRepository(db)
    listing_repo = MarketListingRepository(db)
    processed = 0

    # Separar produtos que precisam de pesquisa
    products_to_search = []
    for product in products:
        if skip_existing and product.market_analysis is not None:
            processed += 1  # Conta como processado (jÃ¡ tem dados)
            continue
        products_to_search.append(product)

    already_done = len(products) - len(products_to_search)

    if not products_to_search:
        logger.info("Market [Apify Batch]: todos os produtos jÃ¡ tÃªm dados")
        return processed

    logger.info(
        "Market [Apify Batch]: %d produtos para pesquisar (%d jÃ¡ existentes, skip=True)",
        len(products_to_search), already_done
    )

    # Mapear produto â query
    product_queries: dict[str, str] = {
        str(p.id): build_search_query(p.search_name)
        for p in products_to_search
    }

    # ââ Processar em micro-batches com save incremental âââââââââââââââââââââââ
    total_batches = (len(products_to_search) + MICRO_BATCH_SIZE - 1) // MICRO_BATCH_SIZE

    for batch_num, batch_start in enumerate(range(0, len(products_to_search), MICRO_BATCH_SIZE), start=1):
        batch_products = products_to_search[batch_start : batch_start + MICRO_BATCH_SIZE]
        batch_end = batch_start + len(batch_products)

        logger.info(
            "Market [Apify Batch]: micro-batch %d/%d | produtos %d-%d de %d",
            batch_num, total_batches, batch_start + 1, batch_end, len(products_to_search)
        )

        # Queries Ãºnicas deste micro-batch
        micro_queries_map: dict[str, str] = {
            str(p.id): product_queries[str(p.id)] for p in batch_products
        }
        unique_micro_queries = list(dict.fromkeys(micro_queries_map.values()))

        # ââ Fase 1 do micro-batch: busca principal ââââââââââââââââââââââââââââ
        batch_results = search_listings_batch(
            queries=unique_micro_queries,
            api_token=settings.APIFY_API_TOKEN,
            actor_id=settings.APIFY_ML_ACTOR_ID,
            max_pages=1,
        )

        products_needing_fallback: list[tuple[Product, str, str]] = []

        for product in batch_products:
            pid = str(product.id)
            query = micro_queries_map[pid]
            apify_result = batch_results.get(query)

            if apify_result is None or not apify_result.listings:
                if apify_result and apify_result.api_errors:
                    logger.warning(
                        "Market [Apify]: '%s' â erros API: %s",
                        query, "; ".join(apify_result.api_errors)
                    )
                simplified = _simplify_query(query)
                if simplified != query:
                    products_needing_fallback.append((product, query, simplified))
                else:
                    logger.info("Market [Apify]: '%s' â 0 resultados, sem fallback possÃ­vel", query)
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

        # ââ Fase 2 do micro-batch: fallback com query simplificada ââââââââââââ
        if products_needing_fallback:
            logger.info(
                "Market [Apify Batch]: micro-batch %d/%d â %d produtos para fallback",
                batch_num, total_batches, len(products_needing_fallback)
            )

            fallback_queries = list(dict.fromkeys(
                simplified for _, _, simplified in products_needing_fallback
            ))

            fallback_results = search_listings_batch(
                queries=fallback_queries,
                api_token=settings.APIFY_API_TOKEN,
                actor_id=settings.APIFY_ML_ACTOR_ID,
                max_pages=1,
            )

            for product, original_query, simplified_query in products_needing_fallback:
                apify_result = fallback_results.get(simplified_query)

                if apify_result is None or not apify_result.listings:
                    logger.info(
                        "Market [Apify]: '%s' â 0 resultados (original='%s' + fallback='%s')",
                        product.search_name, original_query, simplified_query
                    )
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
                else:
                    logger.info(
                        "Market [Apify]: '%s' â fallback sem matching suficiente",
                        product.search_name
                    )

        # Checkpoint: DB jÃ¡ tem o commit de cada _save_market_result acima.
        # Log do progresso acumulado para monitoramento.
        logger.info(
            "Market [Apify Batch]: checkpoint micro-batch %d/%d | "
            "%d/%d produtos com dados atÃ© agora",
            batch_num, total_batches,
            processed - already_done, len(products_to_search)
        )

    logger.info(
        "Market [Apify Batch]: concluÃ­do | %d/%d produtos com dados de mercado",
        processed, len(products)
    )
    return processed


def _process_apify_result(
    product: Product,
    query: str,
    apify_result,
    ml_access_token: str | None,
    is_fallback: bool = False,
    original_search_name: str | None = None,
) -> dict | None:
    """
    Processa resultado Apify de um produto: matching â enriquecimento â agregaÃ§Ã£o.

    Reutilizado tanto para queries principais quanto para fallbacks.

    Args:
        is_fallback:          Se True, aplica confianÃ§a mÃ­nima maior (query mais ampla = mais ruÃ­do)
        original_search_name: Nome original do produto (usado no matching quando Ã© fallback)
    """
    from app.integrations.apify_ml import to_ml_listings
    from app.integrations.mercadolivre import aggregate_market_data, enrich_listings_with_sold_quantity
    from app.services.ml_matching import filter_qualified_listings

    search_name = original_search_name or product.search_name

    if not apify_result or not apify_result.listings:
        return None

    logger.info(
        "Market [Apify]: '%s' â %d listings brutos | total ML: %s%s",
        query,
        apify_result.total_found,
        apify_result.total_results_str or "?",
        " [FALLBACK]" if is_fallback else "",
    )

    # Converter para MLListings (interface comum com ML API direta)
    ml_listings = to_ml_listings(apify_result.listings)

    # ConfianÃ§a mÃ­nima maior no fallback â query mais ampla traz mais ruÃ­do
    min_confidence = settings.ML_MIN_MATCH_CONFIDENCE
    if is_fallback:
        min_confidence = min(min_confidence + 0.10, 0.90)

    # Matching â sem filtro de vendas aqui (sold_quantity=0 antes do enriquecimento)
    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_listings,
        min_sales=0,
        min_confidence=min_confidence,
    )

    if not qualified:
        logger.info(
            "Market [Apify]: '%s' â 0 anÃºncios apÃ³s matching (confianÃ§a >= %.0f%%)%s",
            query, min_confidence * 100,
            " [FALLBACK]" if is_fallback else "",
        )
        return None

    # Enriquecimento com sold_quantity via ML Items API
    # Nota: sold_quantity = 0 para catalog IDs (/p/MLB...) â ML bloqueia para devs nÃ£o certificados.
    # O enriquecimento continua para capturar casos onde retorna listing IDs individuais.
    max_enrich = 10 if is_fallback else 20
    logger.info(
        "Market [Apify]: enriquecendo %d/%d listings com sold_quantity (max=%d)",
        min(len(qualified), max_enrich), len(qualified), max_enrich)
    qualified = enrich_listings_with_sold_quantity(
        listings=qualified,
        access_token=ml_access_token,
        max_to_enrich:min(len(qualified), max_enrich),
        delay_seconds=0.15,
    )

    # Filtro de vendas apÃ³ enriquecimento
    if settings.MIN_SALES_THRESHOLD > 0:
        before_filter = len(qualified)
        qualified_filtered = [q for q in qualified if q.sold_quantity >= settings.MIN_SALES_THRESHOLD]
        matches_filtered = [
            m for q, m in zip(qualified, matches)
            if q.sold_quantity >= settings.MIN_SALES_THRESHOLD
        ]

        if qualified_filtered:
            qualified = qualified_filtered
            matches = matches_filtered
            logger.info(
                "Market [Apify]: '%s' â %d/%d passaram no filtro de vendas >= %d",
                query, len(qualified), before_filter, settings.MIN_SALES_THRESHOLD
            )
        else:
            # 0 passaram no filtro â usar todos (sold_quantity indisponÃ­vel ou produto novo)
            logger.info(
                "Market [Apify]: '%s' â 0 anÃºncios com vendas >= %d, "
                "usando todos os %d qualificados por matching",
                query, settings.MIN_SALES_THRESHOLD, len(qualified)
            )

    # Log de qualidade dos matches
    high_count = sum(1 for m in matches if m.tier == "HIGH")
    medium_count = sum(1 for m in matches if m.tier == "MEDIUM")
    avg_conf = sum(m.score for m in matches) / len(matches) if matches else 0
    total_sold = sum(q.sold_quantity for q in qualified)

    logger.info(
        "Market [Apify]: '%s' â %d qualificados | HIGH=%d MEDIUM=%d | "
        "confianÃ§a=%.0f%% | total_vendas=%d",
        query, len(qualified), high_count, medium_count, avg_conf * 100, total_sold
    )

    for idx, (listing, match) in enumerate(zip(qualified[:3], matches[:3])):
        logger.debug(
            "Market [Apify]: top%d â '%s' | R$ %.2f | %d vendas | match=%.0f%%",
            idx + 1, listing.title[:50], listing.price,
            listing.sold_quantity, match.score * 100
        )

    # Agregar estatÃ­sticas de mercado
    result = aggregate_market_data(qualified, matches)

    # Top 5 listings com links para persistÃªncia
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


def _save_market_result(
    repo: MarketAnalysisRepository,
    listing_repo: MarketListingRepository,
    product: Product,
    market_data: dict,
) -> None:
    """Persiste resultado de mercado (MarketAnalysis + top listings) no banco."""
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
        listing_repo.replace_listings(
            product_id=product.id,
            listings=top_listings,
        )
        logger.info(
            "Market [Apify]: '%s' â %d links ML salvos",
            product.search_name[:40], len(top_listings)
        )


# ââ EstratÃ©gia 2: ML API direta (fallback) ââââââââââââââââââââââââââââââââââââ

def _research_with_ml_api(
    db: Session,
    products: list[Product],
    access_token: str | None,
    skip_existing: bool = True,
) -> int:
    """Pesquisa usando ML API direta (provÃ¡vel bloqueio 403, mantido como fallback)."""
    if access_token:
        logger.info("Market [ML API]: autenticado com OAuth App Token")
    else:
        logger.warning(
            "Market [ML API]: sem token â sold_quantity serÃ¡ 0. "
            "Configure APIFY_API_TOKEN para dados reais."
        )

    effective_min_sales = settings.MIN_SALES_THRESHOLD if access_token else 0
    repo = MarketAnalysisRepository(db)
    processed = 0

    for i, product in enumerate(products):
        if skip_existing and product.market_analysis is not None:
            processed += 1
            continue

        logger.info(
            "Market [ML API]: produto %d/%d | '%s'",
            i + 1, len(products), product.search_name
        )

        try:
            market_data = _research_product_ml_api(
                product=product,
                access_token=access_token,
                effective_min_sales=effective_min_sales,
            )

            if market_data is None:
                logger.warning(
                    "Market [ML API]: nenhum dado para '%s'",
                    product.search_name
                )
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
            logger.error(
                "Market [ML API]: erro ao pesquisar '%s': %s",
                product.search_name, exc, exc_info=True
            )

        finally:
            if i < len(products) - 1:
                time.sleep(settings.ML_REQUEST_DELAY_SECONDS)

    logger.info(
        "Market [ML API]: concluÃ­do | %d/%d produtos com dados",
        processed, len(products)
    )
    return processed


def _research_product_ml_api(
    product: Product,
    access_token: str | None,
    effective_min_sales: int = 0,
) -> dict | None:
    """Pesquisa via ML API direta (lÃ³gica original preservada)."""
    from app.integrations.mercadolivre import aggregate_market_data, search_listings
    from app.services.ml_matching import build_search_query, filter_qualified_listings

    search_name = product.search_name
    query = build_search_query(search_name)

    logger.info("Market [ML API]: query='%s' (original='%s')", query, search_name)

    ml_result = search_listings(query=query, access_token=access_token)

    if ml_result.api_errors:
        logger.warning(
            "Market [ML API]: '%s' â erros: %s",
            query, "; ".join(ml_result.api_errors)
        )

    if not ml_result.listings:
        logger.info("Market [ML API]: '%s' â 0 anÃºncios", query)
        return None

    logger.info(
        "Market [ML API]: '%s' â %d anÃºncios em %d pÃ¡gina(s)",
        query, ml_result.total_found, ml_result.pages_fetched
    )

    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_result.listings,
        min_sales=effective_min_sales,
        min_confidence=settings.ML_MIN_MATCH_CONFIDENCE,
    )

    if not qualified:
        logger.info(
            "Market [ML API]: '%s' â 0 qualificados "
            "(vendas >= %d, confianÃ§a >= %.0f%%)",
            query, settings.MIN_SALES_THRESHOLD, settings.ML_MIN_MATCH_CONFIDENCE * 100
        )
        simplified = _simplify_query(query)
        if simplified != query:
            return _retry_ml_api_simplified(
                product=product,
                simplified_query=simplified,
                access_token=access_token,
                search_name=search_name,
                effective_min_sales=effective_min_sales,
            )
        return None

    high_count = sum(1 for m in matches if m.tier == "HIGH")
    medium_count = sum(1 for m in matches if m.tier == "MEDIUM")
    avg_confidence = sum(m.score for m in matches) / len(matches)

    logger.info(
        "Market [ML API]: '%s' â %d qualificados | HIGH=%d MEDIUM=%d | confianÃ§a=%.0f%%",
        query, len(qualified), high_count, medium_count, avg_confidence * 100
    )

    for i, (listing, match) in enumerate(zip(qualified[:3], matches[:3])):
        logger.debug(
            "Market [ML API]: top%d â '%s' | R$ %.2f | %d vendas | match=%.0f%%",
            i + 1, listing.title[:50], listing.price, listing.sold_quantity, match.score * 100
        )

    return aggregate_market_data(qualified, matches)


def _retry_ml_api_simplified(
    product: Product,
    simplified_query: str,
    access_token: str | None,
    search_name: str,
    effective_min_sales: int = 0,
) -> dict | None:
    """Segunda tentativa com query simplificada via ML API."""
    from app.integrations.mercadolivre import aggregate_market_data, search_listings
    from app.services.ml_matching import filter_qualified_listings

    logger.info("Market [ML API]: fallback query='%s'", simplified_query)

    ml_result = search_listings(query=simplified_query, access_token=access_token)

    if not ml_result.listings:
        return None

    min_confidence_fallback = min(settings.ML_MIN_MATCH_CONFIDENCE + 0.10, 0.90)

    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_result.listings,
        min_sales=effective_min_sales,
        min_confidence=min_confidence_fallback,
    )

    if not qualified:
        logger.info("Market [ML API]: fallback '%s' â 0 qualificados", simplified_query)
        return None

    logger.info(
        "Market [ML API]: fallback '%s' â %d qualificados (confianÃ§a >= %.0f%%)",
        simplified_query, len(qualified), min_confidence_fallback * 100
    )

    return aggregate_market_data(qualified, matches)


# ââ UtilitÃ¡rios âââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def _try_get_ml_token() -> str | None:
    """Tenta obter token ML. Retorna None silenciosamente se nÃ£o configurado."""
    if not settings.ML_APP_ID or not settings.ML_CLIENT_SECRET :
        return None
    from app.integrations.mercadolivre import get_app_token
    return get_app_token(
        app_id=settings.ML_APP_ID,
        client_secret=settings.ML_CLIENT_SECRET,
    )


def _simplify_query(query: str) -> str:
    """Reduz query para 3 primeiros tokens quando nÃ£o encontra resultados."""
    tokens = query.split()
    if len(tokens) <= 3:
        return query
    return " ".join(tokens[:3])
