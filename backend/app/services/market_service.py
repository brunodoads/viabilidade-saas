"""
Market Service — Pesquisa de mercado real no Mercado Livre.

Fluxo por produto:
    1. Construir query otimizada a partir do nome
    2. Buscar no ML com autenticação OAuth (se configurado)
    3. Filtrar anúncios: sold_quantity >= MIN_SALES_THRESHOLD
    4. Aplicar matching: descartar anúncios que não correspondem ao produto
    5. Calcular estatísticas sobre anúncios aprovados
    6. Persistir MarketAnalysis no banco

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

    Execução sequencial com throttle — ML limita rate de requisições.
    Falha individual por produto é logada mas não interrompe o batch.

    Returns:
        Número de produtos pesquisados com sucesso (com dados de mercado)
    """
    from app.integrations.mercadolivre import get_app_token

    # Obter token uma vez para todo o batch
    access_token = get_app_token(
        app_id=settings.ML_APP_ID,
        client_secret=settings.ML_CLIENT_SECRET,
    )

    if access_token:
        logger.info("Market: autenticado no ML (OAuth App Token)")
    else:
        logger.warning(
            "Market: sem token ML — sold_quantity sera 0 em todos anuncios. "
            "Usando min_sales=0 para nao filtrar tudo. "
            "Configure ML_APP_ID e ML_CLIENT_SECRET para dados reais de vendas."
        )

    # Sem autenticacao: ML nao retorna sold_quantity real (fica 0).
    # Usar min_sales=0 para nao eliminar todos os anuncios.
    # Com autenticacao: usar threshold configurado (ex: 1000).
    effective_min_sales = settings.MIN_SALES_THRESHOLD if access_token else 0

    repo = MarketAnalysisRepository(db)
    processed = 0

    for i, product in enumerate(products):
        logger.info(
            "Market: produto %d/%d | '%s'",
            i + 1, len(products), product.search_name
        )

        try:
            market_data = _research_product(
                product=product,
                access_token=access_token,
                effective_min_sales=effective_min_sales,
            )

            if market_data is None:
                logger.warning(
                    "Market: nenhum dado de mercado para '%s'",
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
                "Market: erro ao pesquisar '%s': %s",
                product.search_name, exc, exc_info=True
            )

        finally:
            # Throttle entre produtos — evitar rate limit
            if i < len(products) - 1:
                time.sleep(settings.ML_REQUEST_DELAY_SECONDS)

    logger.info(
        "Market: concluído | %d/%d produtos com dados de mercado",
        processed, len(products)
    )
    return processed


def _research_product(
    product: Product,
    access_token: str | None,
    effective_min_sales: int = 0,
) -> dict | None:
    """
    Pesquisa um produto específico no ML e retorna estatísticas de mercado.

    Pipeline:
        1. Construir query otimizada
        2. Buscar no ML
        3. Filtrar por vendas mínimas
        4. Aplicar matching de produto
        5. Agregar resultados

    Returns:
        Dict com estatísticas ou None se nenhum resultado qualificado
    """
    from app.integrations.mercadolivre import aggregate_market_data, search_listings
    from app.services.ml_matching import build_search_query, filter_qualified_listings

    # 1. Construir query
    search_name = product.search_name
    query = build_search_query(search_name)

    logger.info("Market: query='%s' (original='%s')", query, search_name)

    # 2. Buscar no ML
    ml_result = search_listings(
        query=query,
        access_token=access_token,
    )

    if ml_result.api_errors:
        logger.warning(
            "Market: '%s' — erros de API: %s",
            query, "; ".join(ml_result.api_errors)
        )

    if not ml_result.listings:
        logger.info("Market: '%s' → 0 anúncios retornados pela API", query)
        return None

    logger.info(
        "Market: '%s' → %d anúncios brutos em %d página(s)",
        query, ml_result.total_found, ml_result.pages_fetched
    )

    # 3 + 4. Filtrar por vendas mínimas e matching
    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_result.listings,
        min_sales=effective_min_sales,
        min_confidence=settings.ML_MIN_MATCH_CONFIDENCE,
    )

    if not qualified:
        logger.info(
            "Market: '%s' → 0 anúncios após filtros "
            "(vendas >= %d e matching >= %.0f%%)",
            query, settings.MIN_SALES_THRESHOLD, settings.ML_MIN_MATCH_CONFIDENCE * 100
        )
        # Tentar query mais simples se 0 resultado
        simplified = _simplify_query(query)
        if simplified != query:
            return _retry_with_simplified_query(
                product=product,
                simplified_query=simplified,
                access_token=access_token,
                search_name=search_name,
                effective_min_sales=effective_min_sales,
            )
        return None

    # Log de qualidade do matching
    high_count = sum(1 for m in matches if m.tier == "HIGH")
    medium_count = sum(1 for m in matches if m.tier == "MEDIUM")
    avg_confidence = sum(m.score for m in matches) / len(matches)

    logger.info(
        "Market: '%s' → %d qualificados | HIGH=%d MEDIUM=%d | confiança_média=%.0f%%",
        query, len(qualified), high_count, medium_count, avg_confidence * 100
    )

    # Log dos top 3 matches aprovados para debug
    for i, (listing, match) in enumerate(zip(qualified[:3], matches[:3])):
        logger.debug(
            "Market: top%d → '%s' | R$ %.2f | %d vendas | match=%.0f%%",
            i + 1, listing.title[:50], listing.price, listing.sold_quantity, match.score * 100
        )

    # 5. Agregar estatísticas (passa matches para calcular avg_match_confidence)
    return aggregate_market_data(qualified, matches)


def _simplify_query(query: str) -> str:
    """
    Reduz a query para os 3 primeiros tokens quando não encontra resultados.

    Estratégia de fallback: query longa pode ser muito específica para o ML.
    """
    tokens = query.split()
    if len(tokens) <= 3:
        return query
    return " ".join(tokens[:3])


def _retry_with_simplified_query(
    product: Product,
    simplified_query: str,
    access_token: str | None,
    search_name: str,
    effective_min_sales: int = 0,
) -> dict | None:
    """
    Segunda tentativa com query simplificada — mais ampla, pode trazer mais resultados.

    Penalidade: matching precisa ser mais rigoroso para compensar a query ampla.
    """
    from app.integrations.mercadolivre import aggregate_market_data, search_listings
    from app.services.ml_matching import filter_qualified_listings

    logger.info("Market: fallback com query simplificada='%s'", simplified_query)

    ml_result = search_listings(
        query=simplified_query,
        access_token=access_token,
    )

    if not ml_result.listings:
        return None

    # Matching mais rigoroso na segunda tentativa (query mais ampla → mais falsos positivos)
    min_confidence_fallback = min(settings.ML_MIN_MATCH_CONFIDENCE + 0.10, 0.90)

    qualified, matches = filter_qualified_listings(
        catalog_name=search_name,
        listings=ml_result.listings,
        min_sales=effective_min_sales,
        min_confidence=min_confidence_fallback,
    )

    if not qualified:
        logger.info("Market: fallback '%s' → 0 resultados qualificados", simplified_query)
        return None

    logger.info(
        "Market: fallback '%s' → %d qualificados (confiança >= %.0f%%)",
        simplified_query, len(qualified), min_confidence_fallback * 100
    )

    return aggregate_market_data(qualified, matches)
