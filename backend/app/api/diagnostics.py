"""
Diagnostics API — Visibilidade interna do pipeline por catálogo.

Endpoints de diagnóstico para entender exatamente onde o pipeline
está quebrando (parsing → market → finance → scoring).

NÃO expõe dados sensíveis — apenas contagens e amostras para debug.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.exceptions import NotFoundException
from app.db.session import get_db
from app.models.analysis import FinancialAnalysis, MarketAnalysis, OpportunityScore
from app.models.catalog import Catalog
from app.models.product import Product
from app.models.user import User
from app.repositories.catalog_repo import CatalogRepository

router = APIRouter(prefix="/diagnostics", tags=["Diagnóstico"])


@router.get(
    "/catalog/{catalog_id}",
    summary="Diagnóstico do pipeline de um catálogo",
    description=(
        "Retorna contagens de registros em cada etapa do pipeline e amostras "
        "de produtos para identificar onde o processamento está quebrando."
    ),
)
def diagnose_catalog(
    catalog_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Mostra quantos produtos passaram por cada etapa do pipeline.

    Interpretação:
        products == market == finance == scores → pipeline OK
        products > 0, market == 0              → ML não retornou dados (auth? matching?)
        market > 0, finance == 0               → finance_service com erro
        finance > 0, scores == 0               → strategy_service com erro
    """
    # Verificar se o catálogo existe e pertence ao usuário
    catalog_repo = CatalogRepository(db)
    catalog = catalog_repo.get_by_id_and_user(catalog_id=catalog_id, user_id=current_user.id)
    if catalog is None:
        raise NotFoundException("Catálogo")

    # ── Contagens por etapa ───────────────────────────────────────────────────
    total_products = (
        db.query(func.count(Product.id))
        .filter(Product.catalog_id == catalog_id)
        .scalar() or 0
    )

    with_market = (
        db.query(func.count(MarketAnalysis.id))
        .join(Product, MarketAnalysis.product_id == Product.id)
        .filter(Product.catalog_id == catalog_id)
        .scalar() or 0
    )

    with_finance = (
        db.query(func.count(FinancialAnalysis.id))
        .join(Product, FinancialAnalysis.product_id == Product.id)
        .filter(Product.catalog_id == catalog_id)
        .scalar() or 0
    )

    with_score = (
        db.query(func.count(OpportunityScore.id))
        .join(Product, OpportunityScore.product_id == Product.id)
        .filter(Product.catalog_id == catalog_id)
        .scalar() or 0
    )

    # ── Amostra de produtos (primeiros 10) ────────────────────────────────────
    sample_products = (
        db.query(Product)
        .filter(Product.catalog_id == catalog_id)
        .limit(10)
        .all()
    )

    product_samples = []
    for p in sample_products:
        ma = p.market_analysis
        fa = p.financial_analysis
        sc = p.opportunity_score

        product_samples.append({
            "raw_name": p.raw_name,
            "search_name": p.search_name,
            "cost": float(p.cost),
            "has_market": ma is not None,
            "has_finance": fa is not None,
            "has_score": sc is not None,
            # Dados de mercado (se disponíveis)
            "market": {
                "avg_price": float(ma.avg_price) if ma else None,
                "total_sellers": ma.total_sellers if ma else None,
                "listings_found": ma.total_listings_found if ma else None,
                "avg_confidence": float(ma.avg_match_confidence) if ma and ma.avg_match_confidence else None,
            } if ma else None,
            # Dados financeiros (se disponíveis)
            "finance": {
                "unit_cost_used": float(fa.cost) if fa else None,
                "avg_market_price": float(fa.avg_market_price) if fa else None,
                "gross_margin_pct": float(fa.gross_margin_pct) if fa else None,
                "is_viable": fa.is_viable if fa else None,
            } if fa else None,
            # Score (se disponível)
            "score": {
                "final_score": float(sc.final_score) if sc else None,
                "recommendation": sc.recommendation.value if sc else None,
            } if sc else None,
        })

    # ── Diagnóstico automático ────────────────────────────────────────────────
    bottleneck = _identify_bottleneck(total_products, with_market, with_finance, with_score)

    return {
        "catalog_id": str(catalog_id),
        "catalog_status": catalog.status.value,
        "pipeline_counts": {
            "1_products_extracted": total_products,
            "2_with_market_analysis": with_market,
            "3_with_financial_analysis": with_finance,
            "4_with_opportunity_score": with_score,
        },
        "pipeline_pct": {
            "market_coverage": round(with_market / total_products * 100, 1) if total_products else 0,
            "finance_coverage": round(with_finance / total_products * 100, 1) if total_products else 0,
            "score_coverage": round(with_score / total_products * 100, 1) if total_products else 0,
        },
        "bottleneck": bottleneck,
        "sample_products": product_samples,
    }


@router.get(
    "/ml-test",
    summary="Testar conectividade com Mercado Livre",
    description=(
        "Faz uma busca real no ML com a query 'teclado multimidia' "
        "e retorna o resultado bruto para diagnóstico de autenticação e conectividade. "
        "NÃO requer catálogo — testa direto a integração ML."
    ),
)
def test_ml_connectivity(
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Diagnóstico rápido da integração ML.

    Retorna:
        - token_status: se conseguiu obter token OAuth
        - search_status: resultado da busca
        - total_raw_listings: quantos anúncios a API retornou
        - sample_listings: primeiros 3 anúncios brutos
        - api_errors: erros reportados pela integração
        - query_used: query de teste usada
    """
    from app.core.config import settings
    from app.integrations.mercadolivre import get_app_token, search_listings

    test_query = "teclado multimidia"

    # 1. Testar autenticação
    try:
        token = get_app_token(
            app_id=settings.ML_APP_ID,
            client_secret=settings.ML_CLIENT_SECRET,
        )
        token_status = "OK — token obtido" if token else "FALHOU — credenciais inválidas ou ausentes"
        token_preview = (token[:12] + "...") if token else None
    except Exception as exc:
        token_status = f"ERRO — {exc}"
        token = None
        token_preview = None

    # 2. Testar busca
    try:
        result = search_listings(query=test_query, access_token=token)
        search_status = "OK" if result else "FALHOU — search_listings retornou None"

        if result:
            sample = [
                {
                    "title": l.title[:80],
                    "price": float(l.price),
                    "sold_quantity": l.sold_quantity,
                }
                for l in result.listings[:3]
            ]
            return {
                "query_used": test_query,
                "token_status": token_status,
                "token_preview": token_preview,
                "search_status": search_status,
                "total_raw_listings": result.total_found,
                "pages_fetched": result.pages_fetched,
                "api_errors": result.api_errors,
                "sample_listings": sample,
            }
        else:
            return {
                "query_used": test_query,
                "token_status": token_status,
                "token_preview": token_preview,
                "search_status": "FALHOU — search_listings retornou None (erro irrecuperável)",
                "total_raw_listings": 0,
                "pages_fetched": 0,
                "api_errors": ["search_listings retornou None"],
                "sample_listings": [],
            }

    except Exception as exc:
        return {
            "query_used": test_query,
            "token_status": token_status,
            "token_preview": token_preview,
            "search_status": f"ERRO — {exc}",
            "total_raw_listings": 0,
            "pages_fetched": 0,
            "api_errors": [str(exc)],
            "sample_listings": [],
        }


def _identify_bottleneck(products: int, market: int, finance: int, scores: int) -> dict:
    """Identifica automaticamente onde o pipeline está quebrando."""
    if products == 0:
        return {
            "stage": "PARSING",
            "message": "Nenhum produto foi extraído do catálogo.",
            "action": "Verificar o arquivo de catálogo e os logs do scout_service.",
        }
    if market == 0:
        return {
            "stage": "MARKET",
            "message": f"0/{products} produtos têm dados de mercado.",
            "action": (
                "ML não retornou resultados. Causas prováveis: "
                "(1) ML_APP_ID / ML_CLIENT_SECRET não configurados no worker; "
                "(2) queries de busca não retornam resultados; "
                "(3) matching confidence < threshold para todos os produtos."
            ),
        }
    if finance == 0:
        return {
            "stage": "FINANCE",
            "message": f"{market}/{products} têm dados de mercado mas 0 têm análise financeira.",
            "action": "Erro no finance_service. Verificar logs do worker para exceptions.",
        }
    if scores == 0:
        return {
            "stage": "SCORING",
            "message": f"{finance}/{products} têm análise financeira mas 0 têm score.",
            "action": "Erro no strategy_service. Verificar logs do worker para exceptions.",
        }
    if scores < products:
        return {
            "stage": "PARTIAL",
            "message": f"{scores}/{products} produtos foram pontuados ({products - scores} sem dados de mercado).",
            "action": "Pipeline parcialmente funcional. Verificar produtos sem market_analysis.",
        }
    return {
        "stage": "OK",
        "message": f"Pipeline completo: {scores}/{products} produtos pontuados.",
        "action": "Nenhuma ação necessária.",
    }
