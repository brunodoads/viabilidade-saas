"""
Testes do column_detector — detecção de colunas em catálogos reais.

Cobre: normalização de texto, scoring, mapeamento e detecção de cabeçalho.

Execute: pytest tests/test_column_detector.py -v
"""

import pytest

from app.services.column_detector import (
    detect_columns,
    detect_header_row,
    normalize_text,
    score_header,
)


class TestNormalizeText:
    """Testa remoção de acentos e normalização."""

    def test_removes_accents(self):
        assert normalize_text("Descrição") == "descricao"
        assert normalize_text("Preço") == "preco"
        assert normalize_text("Código") == "codigo"

    def test_lowercase(self):
        assert normalize_text("PRODUTO") == "produto"
        assert normalize_text("Nome do Item") == "nome do item"

    def test_strips_spaces(self):
        assert normalize_text("  custo  ") == "custo"

    def test_normalizes_multiple_spaces(self):
        assert normalize_text("nome  do  produto") == "nome do produto"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_unicode_apostrophe(self):
        """Apóstrofo unicode é removido na normalização"""
        result = normalize_text("Preço Unitário")
        assert "unitario" in result


class TestScoreHeader:
    """Testa scoring de cabeçalhos contra campos semânticos."""

    # ── product_name ─────────────────────────────────────────────────────────

    def test_exact_produto(self):
        score, _ = score_header("Produto", "product_name")
        assert score == 1.0

    def test_exact_descricao(self):
        score, _ = score_header("Descrição", "product_name")
        assert score == 1.0

    def test_exact_nome(self):
        score, _ = score_header("Nome", "product_name")
        assert score == 1.0

    def test_compound_nome_produto(self):
        score, _ = score_header("Nome do Produto", "product_name")
        assert score >= 0.80

    def test_compound_desc_item(self):
        score, _ = score_header("Descrição do Item", "product_name")
        assert score >= 0.80

    def test_uppercase_produto(self):
        score, _ = score_header("PRODUTO", "product_name")
        assert score >= 0.95

    def test_item_name(self):
        score, _ = score_header("Item Name", "product_name")
        assert score >= 0.70

    # ── cost ─────────────────────────────────────────────────────────────────

    def test_exact_custo(self):
        score, _ = score_header("Custo", "cost")
        assert score == 1.0

    def test_exact_preco(self):
        score, _ = score_header("Preço", "cost")
        assert score == 1.0

    def test_vlr_custo(self):
        score, _ = score_header("Vlr Custo", "cost")
        assert score >= 0.70

    def test_preco_de_custo(self):
        score, _ = score_header("Preço de Custo", "cost")
        assert score >= 0.80

    def test_valor_unitario(self):
        score, _ = score_header("Valor Unitário", "cost")
        assert score >= 0.75

    def test_unit_price(self):
        score, _ = score_header("Unit Price", "cost")
        assert score >= 0.80

    # ── sku ──────────────────────────────────────────────────────────────────

    def test_exact_sku(self):
        score, _ = score_header("SKU", "sku")
        assert score == 1.0

    def test_exact_codigo(self):
        score, _ = score_header("Código", "sku")
        assert score == 1.0

    def test_cod_produto(self):
        score, _ = score_header("Cod Produto", "sku")
        assert score >= 0.70

    def test_referencia(self):
        score, _ = score_header("Referência", "sku")
        assert score >= 0.80

    # ── Sem match ────────────────────────────────────────────────────────────

    def test_no_match_returns_zero(self):
        score, _ = score_header("Quantidade", "product_name")
        assert score < 0.60

    def test_random_column_no_match(self):
        score, _ = score_header("XYZ123_ABC", "cost")
        assert score < 0.60


class TestDetectColumns:
    """Testa mapeamento completo de colunas."""

    def test_standard_brazilian_headers(self):
        """Headers típicos de catálogo brasileiro."""
        headers = ["Produto", "Custo", "SKU", "Categoria", "Fornecedor"]
        result = detect_columns(headers)
        assert result.product_name == 0
        assert result.cost == 1
        assert result.sku == 2
        assert result.category == 3
        assert result.supplier == 4

    def test_mixed_order(self):
        """Colunas em ordem diferente do esperado."""
        headers = ["Custo", "SKU", "Produto", "Categoria"]
        result = detect_columns(headers)
        assert result.product_name == 2
        assert result.cost == 0
        assert result.sku == 1

    def test_only_required_columns(self):
        """Apenas colunas obrigatórias — sem opcionais."""
        headers = ["Descrição", "Preço"]
        result = detect_columns(headers)
        assert result.has_required_columns is True
        assert result.sku is None
        assert result.category is None
        assert result.supplier is None

    def test_uppercase_headers(self):
        """Cabeçalhos em maiúsculas (comum em catálogos antigos)."""
        headers = ["PRODUTO", "CUSTO", "CÓDIGO"]
        result = detect_columns(headers)
        assert result.product_name == 0
        assert result.cost == 1
        assert result.sku == 2

    def test_compound_headers(self):
        """Cabeçalhos compostos realistas."""
        headers = [
            "Nome do Produto",
            "Preço de Custo",
            "Código do Produto",
            "Grupo de Produto",
        ]
        result = detect_columns(headers)
        assert result.product_name == 0
        assert result.cost == 1
        assert result.sku == 2
        assert result.category == 3

    def test_english_headers(self):
        """Catálogo de fornecedor com headers em inglês."""
        headers = ["Product Name", "Unit Cost", "SKU", "Category"]
        result = detect_columns(headers)
        assert result.product_name == 0
        assert result.cost == 1
        assert result.has_required_columns is True

    def test_missing_required_column(self):
        """Sem coluna de custo — deve falhar."""
        headers = ["Produto", "Quantidade", "SKU"]
        result = detect_columns(headers)
        assert result.has_required_columns is False
        assert result.cost is None

    def test_extra_irrelevant_columns(self):
        """Colunas extras que não devem interferir no mapeamento."""
        headers = [
            "Produto", "Qtd Estoque", "Custo", "Preço Venda", "Margem %", "SKU"
        ]
        result = detect_columns(headers)
        assert result.product_name == 0
        assert result.cost == 2
        assert result.sku == 5

    def test_none_headers_ignored(self):
        """Células de cabeçalho None (colunas mescladas ou vazias)."""
        headers = ["Produto", None, "Custo", None, "SKU"]
        result = detect_columns(headers)
        assert result.product_name == 0
        assert result.cost == 2
        assert result.sku == 4

    def test_missing_optional_fields(self):
        headers = ["Produto", "Custo"]
        result = detect_columns(headers)
        assert "sku" in result.missing_optional
        assert "category" in result.missing_optional
        assert "supplier" in result.missing_optional


class TestDetectHeaderRow:
    """Testa detecção da linha de cabeçalho."""

    def test_header_in_first_row(self):
        rows = [
            ["Produto", "Custo", "SKU"],
            ["Caixa de Papelão", 12.50, "001"],
            ["Frasco Plástico", 5.90, "002"],
        ]
        idx, confidence = detect_header_row(rows)
        assert idx == 0
        assert confidence > 0.5

    def test_header_in_third_row(self):
        """
        Catálogo com logo/título nas primeiras linhas.
        Linha 0: Nome da empresa
        Linha 1: vazia
        Linha 2: cabeçalho real
        """
        rows = [
            ["DISTRIBUIDORA ABC LTDA", None, None],
            [None, None, None],
            ["Produto", "Preço", "Código"],
            ["Item A", 10.00, "001"],
            ["Item B", 20.00, "002"],
        ]
        idx, confidence = detect_header_row(rows)
        assert idx == 2
        assert confidence > 0.3

    def test_all_numeric_rows_no_header(self):
        """Planilha só com números — baixa confiança no cabeçalho."""
        rows = [
            [1, 10.00, 100],
            [2, 20.00, 200],
            [3, 30.00, 300],
        ]
        _, confidence = detect_header_row(rows)
        assert confidence < 0.5
