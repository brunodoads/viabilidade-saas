import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.exceptions import BadRequestException, NotFoundException
from app.db.session import get_db
from app.models.analysis import Recommendation
from app.models.catalog import CatalogStatus
from app.models.user import User
from app.repositories.catalog_repo import CatalogRepository
from app.repositories.product_repo import ProductRepository
from app.repositories.opportunity_repo import MarketListingRepository
from app.schemas.opportunity import (
    FinancialDataResponse,
    MarketDataResponse,
    MarketListingResponse,
    OpportunityListResponse,
    OpportunityResponse,
)

router = APIRouter(prefix="/opportunities", tags=["Oportunidades"])


@router.get(
    "/{catalog_id}",
    response_model=OpportunityListResponse,
    summary="Listar oportunidades de um catálogo",
    description=(
        "Retorna os produtos rankeados por score estratégico. "
        "Disponível apenas quando catalog.status == READY."
    ),
)
def list_opportunities(
    catalog_id: uuid.UUID,
    recommendation: Recommendation | None = Query(
        default=None,
        description="Filtrar por classificação (ALTA, MEDIA, BAIXA, EVITAR)",
    ),
    min_score: float | None = Query(
        default=None,
        ge=0,
        le=100,
        description="Score mínimo (0-100)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OpportunityListResponse:
    """
    Dashboard principal de oportunidades.

    Retorna produtos com scores, dados de mercado e análise financeira.
    Permite filtrar por classificação e score mínimo.
    """
    # Verificar se catálogo existe e pertence ao usuário
    catalog_repo = CatalogRepository(db)
    catalog = catalog_repo.get_by_id_and_user(
        catalog_id=catalog_id, user_id=current_user.id
    )

    if catalog is None:
        raise NotFoundException("Catálogo")

    if catalog.status != CatalogStatus.READY:
        raise BadRequestException(
            f"Catálogo ainda não processado. Status atual: {catalog.status.value}. "
            "Aguarde o processamento completo antes de consultar oportunidades."
        )

    # Carregar produtos com todas as análises (eager loading)
    product_repo = ProductRepository(db)
    products = product_repo.get_by_catalog_with_analyses(catalog_id=catalog_id)

    # Pré-carregar listings para todos os produtos em uma única query
    listing_repo = MarketListingRepository(db)
    from app.models.listing import MarketListing
    product_ids = [p.id for p in products]
    all_listings = (
        db.query(MarketListing)
        .filter(MarketListing.product_id.in_(product_ids))
        .order_by(MarketListing.product_id, MarketListing.rank_position)
        .all()
    )
    listings_by_product: dict = {}
    for listing in all_listings:
        listings_by_product.setdefault(listing.product_id, []).append(listing)

    # Montar response consolidado
    items: list[OpportunityResponse] = []

    for product in products:
        score = product.opportunity_score
        if score is None:
            continue  # Produto sem score — skip (não deveria acontecer se status=READY)

        # Filtros opcionais
        if recommendation and score.recommendation != recommendation:
            continue
        if min_score and float(score.final_score) < min_score:
            continue

        market_data = None
        if product.market_analysis:
            ma = product.market_analysis
            # Montar lista de listings com links ML
            product_listings = listings_by_product.get(product.id, [])
            listing_responses = [
                MarketListingResponse(
                    rank_position=lt.rank_position,
                    item_id=lt.item_id,
                    title=lt.title,
                    price=lt.price,
                    sold_quantity=lt.sold_quantity,
                    permalink=lt.permalink,
                    thumbnail=lt.thumbnail,
                    match_confidence=lt.match_confidence,
                    free_shipping=lt.free_shipping,
                    logistic_type=lt.logistic_type,
                    ml_fee_pct=lt.ml_fee_pct,
                )
                for lt in product_listings
            ]
            market_data = MarketDataResponse(
                avg_price=ma.avg_price,
                min_price=ma.min_price,
                max_price=ma.max_price,
                total_sellers=ma.total_sellers,
                listings_above_threshold=ma.listings_above_threshold,
                avg_ml_fee_pct=ma.avg_ml_fee_pct,
                free_shipping_pct=ma.free_shipping_pct,
                listings=listing_responses,
            )

        financial_data = None
        if product.financial_analysis:
            fa = product.financial_analysis
            financial_data = FinancialDataResponse(
                cost=fa.cost,
                avg_market_price=fa.avg_market_price,
                marketplace_fee_pct=fa.marketplace_fee_pct,
                gross_margin=fa.gross_margin,
                gross_margin_pct=fa.gross_margin_pct,
                break_even_price=fa.break_even_price,
                is_viable=fa.is_viable,
                net_margin=fa.net_margin,
                net_margin_pct=fa.net_margin_pct,
                min_price_for_target_margin=fa.min_price_for_target_margin,
            )

        items.append(
            OpportunityResponse(
                product_id=product.id,
                raw_name=product.raw_name,
                normalized_name=product.normalized_name,
                sku=product.sku,
                category=product.category,
                cost=product.cost,
                final_score=score.final_score,
                rank=score.rank,
                recommendation=score.recommendation,
                demand_score=score.demand_score,
                margin_score=score.margin_score,
                competition_score=score.competition_score,
                market=market_data,
                financial=financial_data,
            )
        )

    # Ordenar por rank (já deveria estar ordenado mas garante)
    items.sort(key=lambda x: x.rank)

    return OpportunityListResponse(
        catalog_id=catalog_id,
        total=len(items),
        items=items,
    )

