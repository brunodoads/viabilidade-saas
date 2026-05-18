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
from app.schemas.opportunity import (
    FinancialDataResponse,
    MarketDataResponse,
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
            market_data = MarketDataResponse(
                avg_price=ma.avg_price,
                min_price=ma.min_price,
                max_price=ma.max_price,
                total_sellers=ma.total_sellers,
                listings_above_threshold=ma.listings_above_threshold,
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
