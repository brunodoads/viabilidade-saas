"""
Testes do Strategy Service (strategy_service.py).

Cobertura:
    - Pesos do score somam 1.0
    - demand_score: tiers de avg_sold_quantity + fallback por listings
    - margin_score: tiers por margem bruta
    - competition_score: tiers por número de vendedores
    - confidence_score: conversão 0-1 → 0-100
    - calculate_final_score: fórmula ponderada
    - classify: thresholds EXCELENTE/BOA/ARRISCADA/EVITAR
    - build_explanation: texto correto por combinação de scores
    - OpportunityFilter: matches() com diferentes critérios
    - score_product: integração completa
    - Ranking: produtos ordenados por final_score DESC
"""

import os
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test_db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-only")
os.environ.setdefault("ML_FEE_PCT", "15.0")

import pytest

from app.models.analysis import Recommendation
from app.services.strategy_service import (
    WEIGHT_COMPETITION,
    WEIGHT_CONFIDENCE,
    WEIGHT_DEMAND,
    WEIGHT_MARGIN,
    OpportunityFilter,
    ScoringResult,
    build_explanation,
    calculate_final_score,
    classify,
    competition_score,
    confidence_score,
    demand_score,
    filter_opportunities,
    margin_score,
    score_product,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_product(
    gross_margin_pct: float = 30.0,
    listings_above_threshold: int = 10,
    total_sellers: int = 8,
    avg_sold_quantity: int | None = 5000,
    avg_match_confidence: float | None = 0.85,
    is_viable: bool = True,
) -> MagicMock:
    """Cria um mock de Product com financial_analysis e market_analysis."""
    product = MagicMock()
    product.id = uuid.uuid4()
    product.search_name = "Produto Teste"
    product.category = None

    fa = MagicMock()
    fa.gross_margin_pct = Decimal(str(gross_margin_pct))
    fa.is_viable = is_viable
    product.financial_analysis = fa

    ma = MagicMock()
    ma.listings_above_threshold = listings_above_threshold
    ma.total_sellers = total_sellers
    ma.avg_sold_quantity = avg_sold_quantity
    ma.avg_match_confidence = Decimal(str(avg_match_confidence)) if avg_match_confidence else None
    product.market_analysis = ma

    product.opportunity_score = None

    return product


# ── Testes de consistência dos pesos ─────────────────────────────────────────

class TestWeights:
    def test_weights_sum_to_one(self):
        """Pesos devem somar exatamente 1.0 — garantia da asserção no módulo."""
        total = WEIGHT_DEMAND + WEIGHT_MARGIN + WEIGHT_COMPETITION + WEIGHT_CONFIDENCE
        assert total == Decimal("1.00")

    def test_margin_has_highest_weight(self):
        """Margem é o fator mais importante para o MVP."""
        assert WEIGHT_MARGIN > WEIGHT_DEMAND
        assert WEIGHT_MARGIN > WEIGHT_COMPETITION
        assert WEIGHT_MARGIN > WEIGHT_CONFIDENCE

    def test_demand_second_highest(self):
        assert WEIGHT_DEMAND > WEIGHT_COMPETITION
        assert WEIGHT_DEMAND > WEIGHT_CONFIDENCE


# ── Testes do demand_score ────────────────────────────────────────────────────

class TestDemandScore:
    def test_very_high_volume_scores_100(self):
        assert demand_score(20, avg_sold_quantity=50_001) == Decimal("100")

    def test_high_volume_tier(self):
        score = demand_score(10, avg_sold_quantity=20_000)
        assert score == Decimal("85")

    def test_medium_high_volume(self):
        score = demand_score(10, avg_sold_quantity=10_000)
        assert score == Decimal("70")

    def test_medium_volume(self):
        score = demand_score(5, avg_sold_quantity=5_000)
        assert score == Decimal("55")

    def test_low_medium_volume(self):
        score = demand_score(3, avg_sold_quantity=2_000)
        assert score == Decimal("38")

    def test_minimum_threshold_volume(self):
        score = demand_score(1, avg_sold_quantity=1_000)
        assert score == Decimal("20")

    def test_below_threshold_volume(self):
        score = demand_score(0, avg_sold_quantity=500)
        assert score == Decimal("5")

    def test_fallback_to_listings_when_no_avg_sold(self):
        """Sem avg_sold_quantity, usa listings_above_threshold (linear)."""
        score_50 = demand_score(50, avg_sold_quantity=None)
        score_25 = demand_score(25, avg_sold_quantity=None)
        score_0 = demand_score(0, avg_sold_quantity=None)
        assert score_50 == Decimal("100")
        assert score_25 == Decimal("50")
        assert score_0 == Decimal("0")

    def test_avg_sold_zero_uses_fallback(self):
        """avg_sold_quantity=0 deve usar fallback por listings."""
        score = demand_score(25, avg_sold_quantity=0)
        # 0 não é > 0, então vai para fallback: 25/50 * 100 = 50
        assert score == Decimal("50")

    def test_avg_sold_takes_priority_over_listings(self):
        """Quando avg_sold disponível, ignora listings_above_threshold."""
        # Mesmo com listings=0 (ruim), avg_sold alto deve dar score alto
        score = demand_score(0, avg_sold_quantity=20_000)
        assert score == Decimal("85")


# ── Testes do margin_score ────────────────────────────────────────────────────

class TestMarginScore:
    def test_excellent_margin_40pct_plus(self):
        assert margin_score(Decimal("40")) == Decimal("100")
        assert margin_score(Decimal("60")) == Decimal("100")

    def test_good_margin_30_to_40(self):
        assert margin_score(Decimal("30")) == Decimal("80")
        assert margin_score(Decimal("35")) == Decimal("80")

    def test_reasonable_margin_20_to_30(self):
        assert margin_score(Decimal("20")) == Decimal("60")
        assert margin_score(Decimal("25")) == Decimal("60")

    def test_low_margin_10_to_20(self):
        assert margin_score(Decimal("10")) == Decimal("35")
        assert margin_score(Decimal("15")) == Decimal("35")

    def test_very_low_margin_0_to_10(self):
        assert margin_score(Decimal("5")) == Decimal("10")
        assert margin_score(Decimal("0.01")) == Decimal("10")

    def test_zero_margin_scores_zero(self):
        assert margin_score(Decimal("0")) == Decimal("0")

    def test_negative_margin_scores_zero(self):
        assert margin_score(Decimal("-5")) == Decimal("0")
        assert margin_score(Decimal("-50")) == Decimal("0")

    def test_boundary_exactly_at_30(self):
        """Exatamente 30% → tier 30-40 (score 80)."""
        assert margin_score(Decimal("30.00")) == Decimal("80")

    def test_boundary_just_below_30(self):
        """29.99% → tier 20-30 (score 60)."""
        assert margin_score(Decimal("29.99")) == Decimal("60")


# ── Testes do competition_score ───────────────────────────────────────────────

class TestCompetitionScore:
    def test_zero_sellers_virgin_market(self):
        assert competition_score(0) == Decimal("100")

    def test_up_to_3_sellers_excellent(self):
        assert competition_score(1) == Decimal("100")
        assert competition_score(3) == Decimal("100")

    def test_4_to_8_sellers_good(self):
        assert competition_score(4) == Decimal("90")
        assert competition_score(8) == Decimal("90")

    def test_9_to_15_sellers_moderate(self):
        assert competition_score(9) == Decimal("75")
        assert competition_score(15) == Decimal("75")

    def test_16_to_25_sellers_low(self):
        assert competition_score(16) == Decimal("55")
        assert competition_score(25) == Decimal("55")

    def test_26_to_40_sellers_crowded(self):
        assert competition_score(26) == Decimal("35")
        assert competition_score(40) == Decimal("35")

    def test_above_40_saturated(self):
        assert competition_score(41) == Decimal("18")
        assert competition_score(100) == Decimal("5")

    def test_very_crowded_market_floor(self):
        """Mercado extremamente saturado tem score mínimo, não zero."""
        score = competition_score(1000)
        assert score == Decimal("5")
        assert score > Decimal("0")


# ── Testes do confidence_score ────────────────────────────────────────────────

class TestConfidenceScore:
    def test_full_confidence_100(self):
        assert confidence_score(1.0) == Decimal("100")

    def test_none_confidence_defaults_to_100(self):
        """Sem dado de confiança → assume máxima (dados confiáveis por padrão)."""
        assert confidence_score(None) == Decimal("100")

    def test_60pct_confidence(self):
        assert confidence_score(0.60) == Decimal("60.00")

    def test_75pct_confidence(self):
        assert confidence_score(0.75) == Decimal("75.00")

    def test_minimum_confidence(self):
        assert confidence_score(0.0) == Decimal("0.00")

    def test_accepts_decimal_input(self):
        assert confidence_score(Decimal("0.85")) == Decimal("85.00")

    def test_clamped_to_100(self):
        """Valor absurdo não deve ultrapassar 100."""
        assert confidence_score(2.0) == Decimal("100")

    def test_clamped_to_zero(self):
        assert confidence_score(-0.5) == Decimal("0")


# ── Testes do calculate_final_score ──────────────────────────────────────────

class TestCalculateFinalScore:
    def test_all_100_gives_100(self):
        score = calculate_final_score(
            Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100")
        )
        assert score == Decimal("100.00")

    def test_all_zero_gives_zero(self):
        score = calculate_final_score(
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        )
        assert score == Decimal("0.00")

    def test_weights_applied_correctly(self):
        """Apenas margem com valor (40%) deve dar score = 40."""
        score = calculate_final_score(
            Decimal("0"),    # demand: 35% × 0 = 0
            Decimal("100"),  # margin: 40% × 100 = 40
            Decimal("0"),    # competition: 15% × 0 = 0
            Decimal("0"),    # confidence: 10% × 0 = 0
        )
        assert score == Decimal("40.00")

    def test_typical_good_product(self):
        """Produto típico BOA: demand=55, margin=80, competition=75, confidence=85."""
        score = calculate_final_score(
            Decimal("55"),  # demand
            Decimal("80"),  # margin
            Decimal("75"),  # competition
            Decimal("85"),  # confidence
        )
        # 55*0.35 + 80*0.40 + 75*0.15 + 85*0.10
        # = 19.25 + 32 + 11.25 + 8.5 = 71
        assert score == Decimal("71.00")

    def test_result_clamped_to_0_100(self):
        """Score não deve ultrapassar 100 ou ficar abaixo de 0."""
        score_high = calculate_final_score(
            Decimal("120"), Decimal("120"), Decimal("120"), Decimal("120")
        )
        assert score_high == Decimal("100.00")

        score_neg = calculate_final_score(
            Decimal("-50"), Decimal("-50"), Decimal("-50"), Decimal("-50")
        )
        assert score_neg == Decimal("0.00")


# ── Testes do classify ────────────────────────────────────────────────────────

class TestClassify:
    def test_75_and_above_excelente(self):
        assert classify(Decimal("75")) == Recommendation.EXCELENTE
        assert classify(Decimal("100")) == Recommendation.EXCELENTE
        assert classify(Decimal("90")) == Recommendation.EXCELENTE

    def test_55_to_74_boa(self):
        assert classify(Decimal("55")) == Recommendation.BOA
        assert classify(Decimal("74")) == Recommendation.BOA
        assert classify(Decimal("60")) == Recommendation.BOA

    def test_35_to_54_arriscada(self):
        assert classify(Decimal("35")) == Recommendation.ARRISCADA
        assert classify(Decimal("54")) == Recommendation.ARRISCADA
        assert classify(Decimal("45")) == Recommendation.ARRISCADA

    def test_below_35_evitar(self):
        assert classify(Decimal("34")) == Recommendation.EVITAR
        assert classify(Decimal("0")) == Recommendation.EVITAR
        assert classify(Decimal("20")) == Recommendation.EVITAR

    def test_boundary_exactly_75(self):
        assert classify(Decimal("75.00")) == Recommendation.EXCELENTE

    def test_boundary_74_99(self):
        assert classify(Decimal("74.99")) == Recommendation.BOA

    def test_boundary_exactly_55(self):
        assert classify(Decimal("55.00")) == Recommendation.BOA

    def test_boundary_54_99(self):
        assert classify(Decimal("54.99")) == Recommendation.ARRISCADA

    def test_boundary_exactly_35(self):
        assert classify(Decimal("35.00")) == Recommendation.ARRISCADA

    def test_boundary_34_99(self):
        assert classify(Decimal("34.99")) == Recommendation.EVITAR


# ── Testes do build_explanation ───────────────────────────────────────────────

class TestBuildExplanation:
    def test_inviable_product_special_message(self):
        expl = build_explanation(
            Decimal("80"), Decimal("0"), Decimal("90"), Decimal("90"),
            is_viable=False,
        )
        assert "inviável" in expl.lower()
        assert "preço" in expl.lower() or "custo" in expl.lower()

    def test_excellent_product_all_good(self):
        expl = build_explanation(
            Decimal("85"),  # alta demanda
            Decimal("100"), # margem excelente
            Decimal("90"),  # baixa concorrência
            Decimal("95"),  # alta confiança
            is_viable=True,
        )
        assert "alta demanda" in expl.lower()
        assert "excelente" in expl.lower()
        assert "baixa concorrência" in expl.lower()

    def test_moderate_demand_mentioned(self):
        expl = build_explanation(
            Decimal("45"),  # demanda moderada
            Decimal("80"),
            Decimal("75"),
            Decimal("90"),
            is_viable=True,
        )
        assert "moderada" in expl.lower()

    def test_limited_demand_mentioned(self):
        expl = build_explanation(
            Decimal("20"),  # demanda limitada
            Decimal("60"),
            Decimal("75"),
            Decimal("90"),
            is_viable=True,
        )
        assert "limitada" in expl.lower()

    def test_high_competition_mentioned(self):
        expl = build_explanation(
            Decimal("55"),
            Decimal("80"),
            Decimal("20"),  # alta concorrência
            Decimal("90"),
            is_viable=True,
        )
        assert "alta concorrência" in expl.lower()

    def test_low_confidence_warning_included(self):
        expl = build_explanation(
            Decimal("55"),
            Decimal("80"),
            Decimal("75"),
            Decimal("45"),  # baixa confiança
            is_viable=True,
        )
        assert "confiança" in expl.lower() or "matching" in expl.lower()

    def test_high_confidence_no_warning(self):
        expl = build_explanation(
            Decimal("55"),
            Decimal("80"),
            Decimal("75"),
            Decimal("90"),  # alta confiança → sem aviso
            is_viable=True,
        )
        assert "confiança" not in expl.lower() or "baixa confiança" not in expl.lower()

    def test_explanation_ends_with_period(self):
        expl = build_explanation(
            Decimal("55"), Decimal("80"), Decimal("75"), Decimal("90"),
            is_viable=True,
        )
        assert expl.endswith(".")

    def test_explanation_starts_uppercase(self):
        expl = build_explanation(
            Decimal("55"), Decimal("80"), Decimal("75"), Decimal("90"),
            is_viable=True,
        )
        assert expl[0].isupper()

    def test_explanation_is_not_empty(self):
        for d in [10, 50, 90]:
            for m in [10, 50, 90]:
                expl = build_explanation(
                    Decimal(str(d)), Decimal(str(m)),
                    Decimal("75"), Decimal("90"),
                    is_viable=True,
                )
                assert len(expl) > 10, f"Explicação muito curta: {expl!r}"


# ── Testes do score_product ───────────────────────────────────────────────────

class TestScoreProduct:
    def test_excellent_product_classified_correctly(self):
        """Alta demanda + boa margem + baixa concorrência → EXCELENTE."""
        product = make_product(
            gross_margin_pct=40.0,
            avg_sold_quantity=20_000,
            total_sellers=3,
            avg_match_confidence=0.90,
        )
        result = score_product(product)
        assert result.recommendation == Recommendation.EXCELENTE
        assert float(result.final_score) >= 75.0

    def test_avoid_product_classified_correctly(self):
        """Margem negativa → EVITAR."""
        product = make_product(
            gross_margin_pct=-5.0,
            is_viable=False,
            avg_sold_quantity=500,
            total_sellers=50,
            avg_match_confidence=0.62,
        )
        result = score_product(product)
        assert result.recommendation == Recommendation.EVITAR
        assert result.margin_score == Decimal("0")

    def test_score_product_returns_scoring_result_type(self):
        product = make_product()
        result = score_product(product)
        assert isinstance(result, ScoringResult)

    def test_score_product_has_explanation(self):
        product = make_product()
        result = score_product(product)
        assert isinstance(result.explanation, str)
        assert len(result.explanation) > 0

    def test_score_product_raises_without_financial_analysis(self):
        product = make_product()
        product.financial_analysis = None
        with pytest.raises(ValueError, match="financial_analysis"):
            score_product(product)

    def test_score_product_raises_without_market_analysis(self):
        product = make_product()
        product.market_analysis = None
        with pytest.raises(ValueError, match="market_analysis"):
            score_product(product)

    def test_to_db_dict_has_all_keys(self):
        product = make_product()
        result = score_product(product)
        d = result.to_db_dict()
        required = [
            "demand_score", "margin_score", "competition_score", "confidence_score",
            "final_score", "recommendation", "explanation",
        ]
        for key in required:
            assert key in d, f"Chave ausente: {key}"


# ── Testes do OpportunityFilter ───────────────────────────────────────────────

class TestOpportunityFilter:
    def _make_product_with_score(
        self,
        final_score: float = 70.0,
        gross_margin_pct: float = 30.0,
        listings: int = 10,
        recommendation: Recommendation = Recommendation.BOA,
        is_viable: bool = True,
    ) -> MagicMock:
        product = MagicMock()
        fa = MagicMock()
        fa.is_viable = is_viable
        fa.gross_margin_pct = Decimal(str(gross_margin_pct))
        product.financial_analysis = fa

        ma = MagicMock()
        ma.listings_above_threshold = listings
        product.market_analysis = ma

        sc = MagicMock()
        sc.final_score = Decimal(str(final_score))
        sc.recommendation = recommendation
        product.opportunity_score = sc

        return product

    def test_default_filter_passes_viable_product(self):
        product = self._make_product_with_score()
        f = OpportunityFilter()
        assert f.matches(product) is True

    def test_min_score_filter(self):
        product = self._make_product_with_score(final_score=60.0)
        f = OpportunityFilter(min_final_score=65.0)
        assert f.matches(product) is False

    def test_min_score_filter_passes_equal(self):
        product = self._make_product_with_score(final_score=65.0)
        f = OpportunityFilter(min_final_score=65.0)
        assert f.matches(product) is True

    def test_min_margin_filter(self):
        product = self._make_product_with_score(gross_margin_pct=15.0)
        f = OpportunityFilter(min_margin_pct=20.0)
        assert f.matches(product) is False

    def test_min_demand_listings_filter(self):
        product = self._make_product_with_score(listings=3)
        f = OpportunityFilter(min_demand_listings=5)
        assert f.matches(product) is False

    def test_recommendation_filter(self):
        product_boa = self._make_product_with_score(recommendation=Recommendation.BOA)
        product_arr = self._make_product_with_score(recommendation=Recommendation.ARRISCADA)
        f = OpportunityFilter(recommendations=[Recommendation.EXCELENTE, Recommendation.BOA])
        assert f.matches(product_boa) is True
        assert f.matches(product_arr) is False

    def test_only_viable_filter_rejects_inviable(self):
        product = self._make_product_with_score(is_viable=False)
        f = OpportunityFilter(only_viable=True)
        assert f.matches(product) is False

    def test_only_viable_false_allows_inviable(self):
        product = self._make_product_with_score(is_viable=False)
        f = OpportunityFilter(only_viable=False, min_final_score=0)
        assert f.matches(product) is True

    def test_no_score_returns_false(self):
        product = self._make_product_with_score()
        product.opportunity_score = None
        f = OpportunityFilter()
        assert f.matches(product) is False

    def test_preset_only_excellent(self):
        f = OpportunityFilter.only_excellent()
        assert f.min_final_score == 75.0
        assert Recommendation.EXCELENTE in f.recommendations
        assert Recommendation.BOA not in f.recommendations

    def test_preset_good_and_above(self):
        f = OpportunityFilter.good_and_above(min_margin=25.0)
        assert f.min_final_score == 55.0
        assert f.min_margin_pct == 25.0
        assert Recommendation.BOA in f.recommendations
        assert Recommendation.EXCELENTE in f.recommendations
        assert Recommendation.ARRISCADA not in f.recommendations


# ── Testes do filter_opportunities ───────────────────────────────────────────

class TestFilterOpportunities:
    def _make_products_mixed(self) -> list:
        """Cria lista de produtos com scores variados."""
        products = []
        configs = [
            (90.0, 40.0, 10, Recommendation.EXCELENTE, True),
            (65.0, 25.0, 15, Recommendation.BOA, True),
            (45.0, 10.0, 5, Recommendation.ARRISCADA, True),
            (20.0, -5.0, 30, Recommendation.EVITAR, False),
        ]
        for final_score, margin, listings, rec, viable in configs:
            product = MagicMock()
            fa = MagicMock()
            fa.is_viable = viable
            fa.gross_margin_pct = Decimal(str(margin))
            product.financial_analysis = fa
            ma = MagicMock()
            ma.listings_above_threshold = listings
            product.market_analysis = ma
            sc = MagicMock()
            sc.final_score = Decimal(str(final_score))
            sc.recommendation = rec
            product.opportunity_score = sc
            products.append(product)
        return products

    def test_filter_returns_only_viable(self):
        products = self._make_products_mixed()
        f = OpportunityFilter(only_viable=True, min_final_score=0)
        result = filter_opportunities(products, f)
        assert len(result) == 3  # exclui o EVITAR/inviável

    def test_filter_by_min_score(self):
        products = self._make_products_mixed()
        f = OpportunityFilter(min_final_score=60.0)
        result = filter_opportunities(products, f)
        assert len(result) == 2  # 90 e 65

    def test_filter_empty_list(self):
        f = OpportunityFilter()
        result = filter_opportunities([], f)
        assert result == []

    def test_filter_preserves_order(self):
        """Filtro não deve reordenar — mantém a ordem da lista de entrada."""
        products = self._make_products_mixed()
        f = OpportunityFilter(only_viable=True, min_final_score=0)
        result = filter_opportunities(products, f)
        # Verifica que a ordem é preservada (não embaralhada)
        scores = [float(p.opportunity_score.final_score) for p in result]
        assert scores == sorted(scores, reverse=True)  # já estão em ordem decrescente
