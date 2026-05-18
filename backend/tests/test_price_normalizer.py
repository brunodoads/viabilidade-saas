"""
Testes do price_normalizer — edge cases reais de catálogos brasileiros.

Execute: pytest tests/test_price_normalizer.py -v
"""

from decimal import Decimal

import pytest

from app.services.price_normalizer import normalize_price, is_valid_cost


class TestNormalizePrice:
    """Testes para normalize_price() — todos os formatos reais encontrados em catálogos."""

    # ── Valores numéricos diretos (openpyxl células numéricas) ───────────────

    def test_integer_cell(self):
        assert normalize_price(100) == Decimal("100")

    def test_float_cell(self):
        assert normalize_price(1234.56) == Decimal("1234.56")

    def test_float_cell_round(self):
        assert normalize_price(50.0) == Decimal("50.0")

    def test_zero_returns_none(self):
        assert normalize_price(0) is None

    def test_negative_returns_none(self):
        assert normalize_price(-100.50) is None

    # ── Formato pt-BR com símbolo de moeda ───────────────────────────────────

    def test_brl_full_format(self):
        """R$ 1.234,56 — formato mais comum em catálogos brasileiros"""
        assert normalize_price("R$ 1.234,56") == Decimal("1234.56")

    def test_brl_no_space(self):
        assert normalize_price("R$1.234,56") == Decimal("1234.56")

    def test_brl_lowercase(self):
        assert normalize_price("r$ 1.234,56") == Decimal("1234.56")

    def test_brl_without_thousands(self):
        assert normalize_price("R$ 234,56") == Decimal("234.56")

    def test_brl_without_decimal(self):
        """R$ 1.234 — sem centavos"""
        assert normalize_price("R$ 1.234") == Decimal("1234")

    def test_brl_simple_comma(self):
        """50,00 — formato simples sem milhar"""
        assert normalize_price("50,00") == Decimal("50.00")

    def test_brl_single_decimal(self):
        """1,5 — uma casa decimal"""
        assert normalize_price("1,5") == Decimal("1.5")

    # ── Formato en-US ────────────────────────────────────────────────────────

    def test_en_us_full(self):
        """1,234.56 — formato americano"""
        assert normalize_price("1,234.56") == Decimal("1234.56")

    def test_en_us_simple(self):
        assert normalize_price("1234.56") == Decimal("1234.56")

    def test_en_us_no_cents(self):
        """1,234 — milhar americano sem centavos"""
        assert normalize_price("1,234") == Decimal("1234")

    # ── Casos ambíguos — regra heurística ────────────────────────────────────

    def test_ambiguous_1234_with_dot(self):
        """
        '1.234' — ambíguo: pt-BR milhar OU en-US decimal?
        Regra: exatamente 3 dígitos após único ponto → trata como milhar pt-BR
        """
        result = normalize_price("1.234")
        assert result == Decimal("1234"), f"Esperado 1234, obtido {result}"

    def test_ambiguous_1234_with_comma(self):
        """
        '1,234' — ambíguo: en-US milhar OU pt-BR decimal?
        Regra: exatamente 3 dígitos após única vírgula → trata como milhar
        """
        result = normalize_price("1,234")
        assert result == Decimal("1234"), f"Esperado 1234, obtido {result}"

    def test_not_ambiguous_1_5(self):
        """'1.5' — claramente decimal"""
        assert normalize_price("1.5") == Decimal("1.5")

    def test_not_ambiguous_1_comma_5(self):
        """'1,5' — claramente decimal pt-BR"""
        assert normalize_price("1,5") == Decimal("1.5")

    # ── Formatos encontrados em catálogos reais ───────────────────────────────

    def test_price_with_leading_spaces(self):
        assert normalize_price("  R$ 100,00  ") == Decimal("100.00")

    def test_price_integer_string(self):
        assert normalize_price("1234") == Decimal("1234")

    def test_multiple_thousands_dots(self):
        """1.234.567 — múltiplos pontos de milhar"""
        assert normalize_price("1.234.567") == Decimal("1234567")

    def test_price_with_tab(self):
        """Célula copiada com tab antes do valor"""
        assert normalize_price("\t99,90") == Decimal("99.90")

    def test_price_with_unicode_space(self):
        """Espaço não-quebrável (U+00A0) entre R$ e valor"""
        assert normalize_price("R$ 1.234,56") == Decimal("1234.56")

    # ── Valores inválidos ────────────────────────────────────────────────────

    def test_none_returns_none(self):
        assert normalize_price(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_price("") is None

    def test_whitespace_returns_none(self):
        assert normalize_price("   ") is None

    def test_text_returns_none(self):
        assert normalize_price("abc") is None

    def test_nan_returns_none(self):
        assert normalize_price("nan") is None

    def test_hash_error_returns_none(self):
        """Células com erro do Excel"""
        assert normalize_price("#N/D") is None
        assert normalize_price("#REF!") is None

    def test_dash_returns_none(self):
        assert normalize_price("-") is None

    def test_zero_string_returns_none(self):
        assert normalize_price("0") is None
        assert normalize_price("0,00") is None


class TestIsValidCost:
    """Testes para is_valid_cost() — validação de custo razoável."""

    def test_valid_cost(self):
        assert is_valid_cost(Decimal("99.90")) is True

    def test_valid_high_cost(self):
        assert is_valid_cost(Decimal("10000.00")) is True

    def test_none_is_invalid(self):
        assert is_valid_cost(None) is False

    def test_zero_is_invalid(self):
        assert is_valid_cost(Decimal("0")) is False

    def test_negative_is_invalid(self):
        assert is_valid_cost(Decimal("-10.00")) is False

    def test_too_high_is_invalid(self):
        assert is_valid_cost(Decimal("9999999.00")) is False

    def test_minimum_valid(self):
        assert is_valid_cost(Decimal("0.01")) is True
