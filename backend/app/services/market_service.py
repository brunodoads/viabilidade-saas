"""
Market Service — Pesquisa de mercado no Mercado Livre.

Estratégia de busca (em ordem de prioridade):
    1. Apify (quando APIFY_API_TOKEN configurado)
       └─ Contorna bloqueio 403 do ML search endpoint
       └─ Após filtragem por matching, enriquece com sold_quantity via ML Items API
    2. ML API direta (quando ML_APP_ID + ML_CLIENT_SECRET configurados)
       └─ Endpoint bloqueado desde 2024 para contas não certificadas
       └─ Mantido como fallback para quando ML liberar acesso oficial
    3. Sem autenticação (último recurso — retorna sold_quantity=0)

Por que dois mecanismos:
    O /sites/MLB/search está bloqueado por política do ML para developers
    sem certificação especial. O endpoint /items/{id} NÃO está bloqueado.
    Apify faz a busca, depois o ML Items API enriquece com sold_quantity.

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
from app.repositories.opportunity_repo import MarketAnalysisRepository

logger = logging.getLogger(__name__)


def research_catalog(db: Session, products: list[Product]) -> int:
    """
    Pesquisa mercado para todos os produtos de um catálogo.

    Tenta Apify primeiro (se APIFY_API_TOKEN configurado), depois ML API direta.
    Falha individual por produto é logada mas não interrompe o batch.

    Returns:
        Número de produtos pesquisados com sucesso (com dados de mercado)
    """
    # Determinar qual estratégia de busca usar
    use_apify = bool(settings.APIFY_API_TOKEN)
    use_ml_api = bool(settings.ML_APP_ID and settings.ML_CLIENT_SECRET)

    if use_apify:
        logger.info(
            "Market: usando Apify para busca ML (contorna bloqueio 403). "
            "Actor: %s", settings.APIFY_ML_ACTOR_ID
        )
        # Obter token ML para enriquecimento com sold_quantity (não obrigatório)
        access_token = _try_get_ml_token()
        if access_token:
            logger.info("Market: token ML disponível para enriquecimento sold_quantity")
        else:
            logger.info(
                "Market: sem token ML — sold_quantity enriquecido via /items/{id} sem auth "
                "(funciona para itens públicos)"
            )
        return _research_with_apify(db, products, access_token)

    elif use_ml_api:
        logger.warning(
            "Market: usando ML API direta (endpoint provavelmente bloqueado). "
            "Configure APIFY_API_TOKEN para resultados confiáveis."
        )
        access_token = _try_get_ml_token()
        return _research_with_ml_api(db, products, access_token)

    else:
        logger.warning(
            "Market: nenhuma integração configurada. "
            "Configure APIFY_API_TOKEN (recomendado) ou ML_APP_ID + ML_CLIENT_SECRET. "
            "Buscando sem autenticação — sold_quantity será 0."
        )
        return _research_with_ml_api(db, products, access_token=None)


# ── Estratégia 1: Apify ───────────────────────────────────────────────────────

def _research_with_apify(
    db: Session,
    products: list[Product],
    ml_access_token: str | None,
) -> int:
    """Pesquisa usando Apify + enriquecimento via ML Items API."""
    repo = MarketAnalysisRepository(db)
    processed = 0

    for i, product in enumerate(products):
        logger.info(
            "Market [Apify]: produto %d/%d | '%s'",
            i + 1, len(products), product.search_name
        )

        try:
            market_data = _research_product_apify(
                product=product,
                ml_access_token=ml_access_token,
            )

            if market_data is None:
                logger.warning(
                    "Market [Apify]: nenhum dado para '%s'",
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
                "Market [Apify]: erro ao pesquisar '%s': %s",
                product.search_name, exc, exc_info=True
            )

        finally:
            # Throttle entre produtos — Apify tem rate limit por token
            if i < len(products) - 1:
                time.sleep(settings.ML_REQUEST_DELAY_SECONDS)

    logger.info(
        "Market [Apify]: concluído | %d/%d produtos com dados de mercado",
        processed, len(products)
    )
    return processed


def _research_product_apify(
    product: Product,
    ml_access_token: str | None,
) -> dict | None:
    """
    Pesquisa um produto via Apify + enriquece com sold_quantity via ML Items API.

    Pipeline:
        1. Construir query otimizada
        2. Buscar via Apify (contorna bloqueio 403)
        3. Converter para MLListings
        4. Filtrar por matching de produto
        5. Enriquecer top matches com sold_quantity via ML Items API
        6. Filtrar por min_sales (após enriquecimento)
        7. Agregar estatísticas
    """
    from app.integrations.apify_ml import search_listings_apify, to_ml_listings
    from app.integrations.mercadolivre import aggregate_market_data, enrich_listings_with_sold_quantity
    from app.services.ml_matching import build_search_query, filter_qualified_listings

    # 1. Construir query
    search_name = product.search_name
    query = build_search_query(search_name)
    logger.info("Market [Apify]: query='%s' (original='%s')", query, search_name)

    # 2. Buscar via Apify
    apify_result = search_listings_apify(
        query=query,
        api_token=settings.APIFY_API_TOKEN,
        actor_id=settings.APIFY_ML_ACTOR_ID,
        max_pages=1,  # ~48 resultados — suficiente para matching
    )

    if apify_result.api_errors:
        logger.warning(
            "Market [Apify]: '%s' — erros: %s",
            query, "; ".join(apify_result.api_errors)
        )

    if not apify_result.listings:
        logger.info("Market [Apify]: '%s' → 0 listings do Apify", query)
        # Tentar query simplificada
        simplified = _simplify_query(query)
        if simplified != query:
            return _retry_apify_with_simplified_query(
                product=product,
                simplified_query=simplified,
                ml_access_token=ml_access_token,
                search_name=search_name,
            )
        return None

    logger.info(
        "Market [Apify]: '%s' → %d listings brutos | total ML: %s",
        query, apify_result.total_found, apify_result.total_results_str or "?"
    )

    # 3. Converter para MLListings (interface comum)
    ml_listings = to_ml_listings(apify_result.listings)

    # 4. Filtrar por matching (sem filtro de vendas ainda — sold_quantity=0)
    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_listings,
        min_sales=0,  # Sem filtro de vendas aqui — enriquecemos depois
        min_confidence=settings.ML_MIN_MATCH_CONFIDENCE,
    )

    if not qualified:
        logger.info(
            "Market [Apify]: '%s' → 0 anúncios após matching (confiança >= %.0f%%)",
            query, settings.ML_MIN_MATCH_CONFIDENCE * 100
        )
        simplified = _simplify_query(query)
        if simplified != query:
            return _retry_apify_with_simplified_query(
                product=product,
                simplified_query=simplified,
                ml_access_token=ml_access_token,
                search_name=search_name,
            )
        return None

    # 5. Enriquecer os qualified listings com sold_quantity via ML Items API
    # Só enriquecemos os que passaram no matching para economizar chamadas
    logger.info(
        "Market [Apify]: enriquecendo %d listings com sold_quantity via ML Items API",
        len(qualified)
    )
    qualified = enrich_listings_with_sold_quantity(
        listings=qualified,
        access_token=ml_access_token,
        max_to_enrich=min(len(qualified), 20),  # Max 20 chamadas por produto
        delay_seconds=0.15,
    )

    # 6. Aplicar filtro de vendas APÓS enriquecimento
    if settings.MIN_SALES_THRESHOLD > 0:
        before_filter = len(qualified)
        qualified_filtered = [q for q in qualified if q.sold_quantity >= settings.MIN_SALES_THRESHOLD]
        matches_filtered = [m for q, m in zip(qualified, matches) if q.sold_quantity >= settings.MIN_SALES_THRESHOLD]

        if qualified_filtered:
            qualified = qualified_filtered
            matches = matches_filtered
            logger.info(
                "Market [Apify]: '%s' → %d/%d passaram no filtro de vendas >= %d",
                query, len(qualified), before_filter, settings.MIN_SALES_THRESHOLD
            )
        else:
            # Se 0 passaram no filtro de vendas, usar todos com dados disponíveis
            # (produto pode ser novo ou dados de vendas indisponíveis)
            logger.info(
                "Market [Apify]: '%s' → 0 anúncios com vendas >= %d, "
                "usando todos os %d qualificados por matching",
                query, settings.MIN_SALES_THRESHOLD, len(qualified)
            )

    # Log de qualidade
    high_count = sum(1 for m in matches if m.tier == "HIGH")
    medium_count = sum(1 for m in matches if m.tier == "MEDIUM")
    avg_conf = sum(m.score for m in matches) / len(matches)
    total_sold = sum(q.sold_quantity for q in qualified)

    logger.info(
        "Market [Apify]: '%s' → %d qualificados | HIGH=%d MEDIUM=%d | "
        "confiança=%.0f%% | total_vendas=%d",
        query, len(qualified), high_count, medium_count, avg_conf * 100, total_sold
    )

    for idx, (listing, match) in enumerate(zip(qualified[:3], matches[:3])):
        logger.debug(
            "Market [Apify]: top%d → '%s' | R$ %.2f | %d vendas | match=%.0f%%",
            idx + 1, listing.title[:50], listing.price,
            listing.sold_quantity, match.score * 100
        )

    # 7. Agregar estatísticas
    return aggregate_market_data(qualified, matches)


def _retry_apify_with_simplified_query(
    product: Product,
    simplified_query: str,
    ml_access_token: str | None,
    search_name: str,
) -> dict | None:
    """Segunda tentativa com query simplificada via Apify."""
    from app.integrations.apify_ml import search_listings_apify, to_ml_listings
    from app.integrations.mercadolivre import aggregate_market_data, enrich_listings_with_sold_quantity
    from app.services.ml_matching import filter_qualified_listings

    logger.info("Market [Apify]: fallback query simplificada='%s'", simplified_query)

    apify_result = search_listings_apify(
        query=simplified_query,
        api_token=settings.APIFY_API_TOKEN,
        actor_id=settings.APIFY_ML_ACTOR_ID,
        max_pages=1,
    )

    if not apify_result.listings:
        return None

    ml_listings = to_ml_listings(apify_result.listings)

    # Matching mais rigoroso na segunda tentativa (query mais ampla)
    min_confidence_fallback = min(settings.ML_MIN_MATCH_CONFIDENCE + 0.10, 0.90)

    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_listings,
        min_sales=0,
        min_confidence=min_confidence_fallback,
    )

    if not qualified:
        logger.info(
            "Market [Apify]: fallback '%s' → 0 resultados qualificados",
            simplified_query
        )
        return None

    qualified = enrich_listings_with_sold_quantity(
        listings=qualified,
        access_token=ml_access_token,
        max_to_enrich=min(len(qualified), 10),
        delay_seconds=0.15,
    )

    logger.info(
        "Market [Apify]: fallback '%s' → %d qualificados (confiança >= %.0f%%)",
        simplified_query, len(qualified), min_confidence_fallback * 100
    )

    return aggregate_market_data(qualified, matches)


# ── Estratégia 2: ML API direta (fallback) ────────────────────────────────────

def _research_with_ml_api(
    db: Session,
    products: list[Product],
    access_token: str | None,
) -> int:
    """Pesquisa usando ML API direta (provável bloqueio 403, mantido como fallback)."""
    if access_token:
        logger.info("Market [ML API]: autenticado com OAuth App Token")
    else:
        logger.warning(
            "Market [ML API]: sem token — sold_quantity será 0. "
            "Configure APIFY_API_TOKEN para dados reais."
        )

    effective_min_sales = settings.MIN_SALES_THRESHOLD if access_token else 0
    repo = MarketAnalysisRepository(db)
    processed = 0

    for i, product in enumerate(products):
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
        "Market [ML API]: concluído | %d/%d produtos com dados",
        processed, len(products)
    )
    return processed


def _research_product_ml_api(
    product: Product,
    access_token: str | None,
    effective_min_sales: int = 0,
) -> dict | None:
    """Pesquisa via ML API direta (lógica original preservada)."""
    from app.integrations.mercadolivre import aggregate_market_data, search_listings
    from app.services.ml_matching import build_search_query, filter_qualified_listings

    search_name = product.search_name
    query = build_search_query(search_name)

    logger.info("Market [ML API]: query='%s' (original='%s')", query, search_name)

    ml_result = search_listings(query=query, access_token=access_token)

    if ml_result.api_errors:
        logger.warning(
            "Market [ML API]: '%s' — erros: %s",
            query, "; ".join(ml_result.api_errors)
        )

    if not ml_result.listings:
        logger.info("Market [ML API]: '%s' → 0 anúncios", query)
        return None

    logger.info(
        "Market [ML API]: '%s' → %d anúncios em %d página(s)",
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
            "Market [ML API]: '%s' → 0 qualificados "
            "(vendas >= %d, confiança >= %.0f%%)",
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
        "Market [ML API]: '%s' → %d qualificados | HIGH=%d MEDIUM=%d | confiança=%.0f%%",
        query, len(qualified), high_count, medium_count, avg_confidence * 100
    )

    for i, (listing, match) in enumerate(zip(qualified[:3], matches[:3])):
        logger.debug(
            "Market [ML API]: top%d → '%s' | R$ %.2f | %d vendas | match=%.0f%%",
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
        logger.info("Market [ML API]: fallback '%s' → 0 qualificados", simplified_query)
        return None

    logger.info(
        "Market [ML API]: fallback '%s' → %d qualificados (confiança >= %.0f%%)",
        simplified_query, len(qualified), min_confidence_fallback * 100
    )

    return aggregate_market_data(qualified, matches)


# ── Utilitários ───────────────────────────────────────────────────────────────

def _try_get_ml_token() -> str | None:
    """Tenta obter token ML. Retorna None silenciosamente se não configurado."""
    if not settings.ML_APP_ID or not settings.ML_CLIENT_SECRET:
        return None
    from app.integrations.mercadolivre import get_app_token
    return get_app_token(
        app_id=settings.ML_APP_ID,
        client_secret=settings.ML_CLIENT_SECRET,
    )


def _simplify_query(query: str) -> str:
    """Reduz query para 3 primeiros tokens quando não encontra resultados."""
    tokens = query.split()
    if len(tokens) <= 3:
        return query
    return " ".join(tokens[:3])
