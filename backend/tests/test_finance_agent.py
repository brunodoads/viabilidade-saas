"""
Testes do Finance Agent (finance_service.py).

Cobertura:
    - FeeConfig: defaults, from_category, from_settings, has_phase2_costs
    - FinancialResult: cálculos de margem, break_even, viabilidade
    - Edge cases: margem negativa, break_even, preço = custo, taxa absurda
    - Precisão decimal: sem erro de ponto flutuante
    - Serialização to_db_dict
    - Fase 2 slots: None no MVP
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test_db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-only")
os.environ.setdefault("ML_FEE_PCT", "15.0")

from decimal import Decimal

import pytest

from app.services.finance_service import (
    FeeConfig,
    FinancialResult,
    _round2,
    calculate,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def default_config() -> FeeConfig:
    return FeeConfig.default()


@pytest.fixture
def standard_result() -> FinancialResult:
    """Produto padrão: custo R$50, preço ML R$100, taxa 15%."""
    return calculate(
        cost=Decimal("50.00"),
        avg_market_price=Decimal("100.00"),
        fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
    )


@pytest.fixture
def tight_margin_result() -> FinancialResult:
    """Produto com margem apertada: custo R$80, preço R$100, taxa 15%."""
    return calculate(
        cost=Decimal("80.00"),
        avg_market_price=Decimal("100.00"),
        fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
    )


@pytest.fixture
def negative_margin_result() -> FinancialResult:
    """Produto inviável: custo R$95, preço R$100, taxa 15%."""
    return calculate(
        cost=Decimal("95.00"),
        avg_market_price=Decimal("100.00"),
        fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
    )


# ── Testes do FeeConfig ───────────────────────────────────────────────────────

class TestFeeConfig:
    def test_default_ml_fee_15pct(self, default_config):
        assert default_config.ml_fee_pct == Decimal("15.00")

    def test_default_phase2_slots_all_zero(self, default_config):
        assert default_config.ads_pct == Decimal("0.00")
        assert default_config.return_rate_pct == Decimal("0.00")
        assert default_config.packaging_cost_brl == Decimal("0.00")
        assert default_config.fulfillment_cost_brl == Decimal("0.00")
        assert default_config.tax_pct == Decimal("0.00")

    def test_default_has_no_phase2_costs(self, default_config):
        assert default_config.has_phase2_costs() is False

    def test_phase2_detected_when_ads_configured(self):
        config = FeeConfig(
            ml_fee_pct=Decimal("15.00"),
            ads_pct=Decimal("3.00"),
        )
        assert config.has_phase2_costs() is True

    def test_phase2_detected_when_packaging_configured(self):
        config = FeeConfig(
            ml_fee_pct=Decimal("15.00"),
            packaging_cost_brl=Decimal("2.50"),
        )
        assert config.has_phase2_costs() is True

    def test_total_variable_pct_mvp(self, default_config):
        # No MVP só taxa ML
        assert default_config.total_variable_pct == Decimal("15.00")

    def test_total_variable_pct_with_ads(self):
        config = FeeConfig(ml_fee_pct=Decimal("15.00"), ads_pct=Decimal("3.00"))
        assert config.total_variable_pct == Decimal("18.00")

    def test_total_fixed_brl_zero_default(self, default_config):
        assert default_config.total_fixed_brl == Decimal("0.00")

    def test_total_fixed_brl_with_costs(self):
        config = FeeConfig(
            packaging_cost_brl=Decimal("2.00"),
            fulfillment_cost_brl=Decimal("3.50"),
        )
        assert config.total_fixed_brl == Decimal("5.50")

    def test_from_settings_reads_env(self):
        """from_settings() deve ler ML_FEE_PCT do ambiente (setado para 15.0 no topo)."""
        config = FeeConfig.from_settings()
        assert config.ml_fee_pct == Decimal("15.0")

    def test_from_category_returns_default_mvp(self):
        """No MVP, from_category deve retornar o mesmo que from_settings."""
        for cat in ["EPI", "Eletrodomésticos", None, "", "Categoria Inexistente"]:
            config = FeeConfig.from_category(cat)
            assert config.ml_fee_pct == Decimal("15.0"), f"Falhou para categoria: {cat!r}"

    def test_fee_config_is_immutable(self, default_config):
        """FeeConfig deve ser frozen=True."""
        with pytest.raises((AttributeError, TypeError)):
            default_config.ml_fee_pct = Decimal("20.00")  # type: ignore

    def test_custom_fee_config(self):
        config = FeeConfig(ml_fee_pct=Decimal("12.00"))
        assert config.ml_fee_pct == Decimal("12.00")
        assert config.ads_pct == Decimal("0.00")  # demais permanecem zero


# ── Testes do cálculo de margem ───────────────────────────────────────────────

class TestFinancialCalculations:
    def test_ml_fee_calculation(self, standard_result):
        """Taxa ML = preço * 15% = R$ 15.00."""
        assert standard_result.ml_fee == Decimal("15.00")

    def test_gross_revenue_equals_price(self, standard_result):
        """Receita bruta = preço médio de mercado."""
        assert standard_result.gross_revenue == Decimal("100.00")

    def test_gross_margin_correct(self, standard_result):
        """Margem = 100 - 50 (custo) - 15 (taxa) = 35."""
        assert standard_result.gross_margin == Decimal("35.00")

    def test_gross_margin_pct_correct(self, standard_result):
        """Margem % = 35 / 100 = 35%."""
        assert standard_result.gross_margin_pct == Decimal("35.00")

    def test_is_viable_positive_margin(self, standard_result):
        assert standard_result.is_viable is True

    def test_negative_margin_is_not_viable(self, negative_margin_result):
        """custo=95 + taxa=15 = 110 > preço=100 → margem negativa."""
        assert negative_margin_result.gross_margin < Decimal("0")
        assert negative_margin_result.is_viable is False

    def test_tight_margin_is_viable(self, tight_margin_result):
        """custo=80 + taxa=15 = 95 < 100 → margem positiva de R$5."""
        assert tight_margin_result.gross_margin == Decimal("5.00")
        assert tight_margin_result.is_viable is True

    def test_tight_margin_pct(self, tight_margin_result):
        """Margem % = 5 / 100 = 5%."""
        assert tight_margin_result.gross_margin_pct == Decimal("5.00")

    def test_zero_margin_not_viable(self):
        """Custo = 85 + taxa = 15 → margem = 0 → não viável."""
        result = calculate(
            cost=Decimal("85.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
        )
        assert result.gross_margin == Decimal("0.00")
        assert result.is_viable is False

    def test_high_margin_product(self):
        """Produto com margem alta: custo=10, preço=100, taxa=15%."""
        result = calculate(
            cost=Decimal("10.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
        )
        assert result.gross_margin == Decimal("75.00")
        assert result.gross_margin_pct == Decimal("75.00")

    def test_different_fee_pct(self):
        """Taxa diferente muda o cálculo corretamente."""
        result_15 = calculate(
            cost=Decimal("50.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
        )
        result_12 = calculate(
            cost=Decimal("50.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=FeeConfig(ml_fee_pct=Decimal("12.00")),
        )
        # Taxa menor → margem maior
        assert result_12.gross_margin > result_15.gross_margin
        assert result_12.gross_margin == Decimal("38.00")


# ── Testes do break-even ──────────────────────────────────────────────────────

class TestBreakEven:
    def test_break_even_formula(self, standard_result):
        """break_even = cost / (1 - 0.15) = 50 / 0.85 ≈ 58.82."""
        expected = Decimal("50.00") / Decimal("0.85")
        assert standard_result.break_even_price == _round2(expected)

    def test_break_even_is_below_avg_price_when_viable(self, standard_result):
        """Se viável, preço médio deve ser maior que break_even."""
        assert standard_result.avg_market_price > standard_result.break_even_price

    def test_break_even_above_avg_price_when_not_viable(self, negative_margin_result):
        """Se inviável, preço médio deve ser menor que break_even."""
        assert negative_margin_result.avg_market_price < negative_margin_result.break_even_price

    def test_price_safety_margin_positive_when_viable(self, standard_result):
        """Margem de segurança deve ser positiva quando produto é viável."""
        assert standard_result.price_safety_margin_pct > Decimal("0")

    def test_price_safety_margin_negative_when_not_viable(self, negative_margin_result):
        """Margem de segurança negativa quando produto não é viável."""
        assert negative_margin_result.price_safety_margin_pct < Decimal("0")

    def test_price_safety_margin_calculation(self, standard_result):
        """Valor correto da margem de segurança."""
        be = standard_result.break_even_price
        price = standard_result.avg_market_price
        expected = _round2((price - be) / be * Decimal("100"))
        assert standard_result.price_safety_margin_pct == expected

    def test_break_even_with_tight_margin(self, tight_margin_result):
        """Produto com margem apertada: break_even próximo do preço."""
        assert tight_margin_result.break_even_price < Decimal("100.00")
        assert tight_margin_result.price_safety_margin_pct > Decimal("0")


# ── Testes de precisão decimal ────────────────────────────────────────────────

class TestDecimalPrecision:
    def test_no_floating_point_error(self):
        """Valores que causariam erro em float devem ser exatos em Decimal."""
        result = calculate(
            cost=Decimal("33.33"),
            avg_market_price=Decimal("99.99"),
            fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
        )
        # Todos os resultados devem ter exatamente 2 casas decimais
        assert result.gross_margin == result.gross_margin.quantize(Decimal("0.01"))
        assert result.gross_margin_pct == result.gross_margin_pct.quantize(Decimal("0.01"))
        assert result.break_even_price == result.break_even_price.quantize(Decimal("0.01"))

    def test_margin_consistency(self, standard_result):
        """gross_margin = gross_revenue - cost - ml_fee (consistência interna)."""
        expected = standard_result.gross_revenue - standard_result.cost - standard_result.ml_fee
        assert _round2(expected) == standard_result.gross_margin

    def test_fee_consistency(self, standard_result):
        """ml_fee = gross_revenue * ml_fee_pct / 100."""
        expected = _round2(
            standard_result.gross_revenue
            * standard_result.fee_config.ml_fee_pct
            / Decimal("100")
        )
        assert expected == standard_result.ml_fee

    def test_result_with_odd_price(self):
        """Preço com muitas casas decimais não deve propagar erro."""
        result = calculate(
            cost=Decimal("17.99"),
            avg_market_price=Decimal("49.90"),
            fee_config=FeeConfig(ml_fee_pct=Decimal("15.00")),
        )
        # 49.90 * 0.15 = 7.485 → arredonda para 7.49
        assert result.ml_fee == Decimal("7.49")
        # margem = 49.90 - 17.99 - 7.49 = 24.42
        assert result.gross_margin == Decimal("24.42")


# ── Testes de serialização ────────────────────────────────────────────────────

class TestToDbDict:
    def test_all_required_keys_present(self, standard_result):
        d = standard_result.to_db_dict()
        required_keys = [
            "cost", "avg_market_price", "marketplace_fee_pct",
            "gross_revenue", "ml_fee", "gross_margin", "gross_margin_pct",
            "break_even_price", "price_safety_margin_pct", "is_viable",
        ]
        for key in required_keys:
            assert key in d, f"Chave ausente: {key}"

    def test_phase2_keys_are_none_by_default(self, standard_result):
        d = standard_result.to_db_dict()
        assert d["ads_cost"] is None
        assert d["return_rate"] is None
        assert d["packaging_cost"] is None
        assert d["fulfillment_cost"] is None
        assert d["tax_cost"] is None
        assert d["net_margin"] is None
        assert d["net_margin_pct"] is None

    def test_marketplace_fee_pct_in_db_dict(self, standard_result):
        d = standard_result.to_db_dict()
        assert d["marketplace_fee_pct"] == Decimal("15.00")

    def test_is_viable_in_db_dict(self, standard_result, negative_margin_result):
        assert standard_result.to_db_dict()["is_viable"] is True
        assert negative_margin_result.to_db_dict()["is_viable"] is False


# ── Testes Fase 2: custos adicionais ─────────────────────────────────────────

class TestPhase2Costs:
    def test_net_margin_calculated_with_ads(self):
        """Quando ADS configurado, net_margin deve ser menor que gross_margin."""
        config = FeeConfig(
            ml_fee_pct=Decimal("15.00"),
            ads_pct=Decimal("5.00"),
        )
        result = calculate(
            cost=Decimal("50.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=config,
        )
        assert result.net_margin is not None
        assert result.net_margin < result.gross_margin
        # net_margin = 35 (bruta) - 5 (ads) = 30
        assert result.net_margin == Decimal("30.00")

    def test_net_margin_with_packaging(self):
        config = FeeConfig(
            ml_fee_pct=Decimal("15.00"),
            packaging_cost_brl=Decimal("2.50"),
        )
        result = calculate(
            cost=Decimal("50.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=config,
        )
        assert result.net_margin is not None
        # net_margin = 35 - 2.50 = 32.50
        assert result.net_margin == Decimal("32.50")

    def test_net_margin_none_without_phase2(self, standard_result):
        """Sem custos Fase 2, net_margin permanece None."""
        assert standard_result.net_margin is None
        assert standard_result.net_margin_pct is None

    def test_net_margin_pct_calculated(self):
        config = FeeConfig(
            ml_fee_pct=Decimal("15.00"),
            ads_pct=Decimal("5.00"),
        )
        result = calculate(
            cost=Decimal("50.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=config,
        )
        assert result.net_margin_pct is not None
        assert result.net_margin_pct == Decimal("30.00")

    def test_ads_cost_in_db_dict_when_configured(self):
        config = FeeConfig(
            ml_fee_pct=Decimal("15.00"),
            ads_pct=Decimal("5.00"),
        )
        result = calculate(
            cost=Decimal("50.00"),
            avg_market_price=Decimal("100.00"),
            fee_config=config,
        )
        d = result.to_db_dict()
        assert d["ads_cost"] == Decimal("5.00")  # 100 * 5% = 5


# ── Testes do summary() ───────────────────────────────────────────────────────

class TestSummary:
    def test_viable_product_summary_contains_viavel(self, standard_result):
        assert "VIÁVEL" in standard_result.summary()

    def test_inviable_product_summary_contains_nao_viavel(self, negative_margin_result):
        assert "NÃO VIÁVEL" in negative_margin_result.summary()

    def test_summary_contains_margin_pct(self, standard_result):
        assert "35.00%" in standard_result.summary()

    def test_summary_contains_break_even(self, standard_result):
        assert "break_even" in standard_result.summary()


# ── Testes da função calculate() ─────────────────────────────────────────────

class TestCalculateFunction:
    def test_calculate_without_config_uses_from_settings(self):
        """calculate() sem config deve usar FeeConfig.from_settings()."""
        result = calculate(
            cost=Decimal("50.00"),
            avg_market_price=Decimal("100.00"),
        )
        assert result.fee_config.ml_fee_pct == Decimal("15.0")
        assert result.gross_margin is not None

    def test_calculate_returns_financial_result_type(self):
        result = calculate(
            cost=Decimal("30.00"),
            avg_market_price=Decimal("80.00"),
        )
        assert isinstance(result, FinancialResult)

    def test_calculate_is_pure_function(self):
        """Mesmo input → mesmo output (sem side effects)."""
        config = FeeConfig(ml_fee_pct=Decimal("15.00"))
        r1 = calculate(Decimal("50"), Decimal("100"), config)
        r2 = calculate(Decimal("50"), Decimal("100"), config)
        assert r1.gross_margin == r2.gross_margin
        assert r1.break_even_price == r2.break_even_price
