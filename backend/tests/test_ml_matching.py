"""
Testes do algoritmo de matching ML — cobertura de edge cases reais.

Cada teste replica uma situação real observada em catálogos brasileiros
vs anúncios do Mercado Livre.

Execute: pytest tests/test_ml_matching.py -v

IMPORTANTE: esses testes são puros (sem banco, sem API) — rodam em ms.
"""

import pytest

from app.services.ml_matching import (
    ConfidenceTier,
    MatchResult,
    _check_clothing_size_mismatch,
    _check_kit_mismatch,
    _check_voltage_mismatch,
    _check_volume_mismatch,
    _normalize,
    _tokenize,
    build_search_query,
    calculate_match_confidence,
)


# ── Helper ────────────────────────────────────────────────────────────────────

def match(catalog: str, ml: str) -> MatchResult:
    """Shortcut para calculate_match_confidence."""
    return calculate_match_confidence(catalog, ml)


# ── Normalização ──────────────────────────────────────────────────────────────

class TestNormalize:
    def test_remove_accents(self):
        assert _normalize("Descrição") == "descricao"
        assert _normalize("Ação") == "acao"
        assert _normalize("São Paulo") == "sao paulo"

    def test_lowercase(self):
        assert _normalize("LUVA DE VINIL") == "luva de vinil"

    def test_remove_special_chars(self):
        assert _normalize("Preço: R$50,00") == "preco r 50 00"

    def test_normalize_spaces(self):
        assert _normalize("luva   de   vinil") == "luva de vinil"

    def test_empty(self):
        assert _normalize("") == ""
        assert _normalize("   ") == ""


class TestTokenize:
    def test_removes_stop_words(self):
        tokens = _tokenize("luva de vinil")
        assert "de" not in tokens
        assert "luva" in tokens
        assert "vinil" in tokens

    def test_keeps_size_tokens(self):
        # Tamanhos de roupa são tokens de 1 char mas devem ser mantidos
        tokens = _tokenize("luva p")
        assert "p" in tokens

    def test_removes_large_numbers(self):
        # Números grandes (> 999) provavelmente são quantidades, não dimensões
        tokens = _tokenize("luva 1000 unid")
        assert "1000" not in tokens

    def test_keeps_dimensions(self):
        tokens = _tokenize("caixa 30 cm")
        assert "30" in tokens  # dimensão pequena deve ficar


# ── Match: produtos equivalentes ─────────────────────────────────────────────

class TestEquivalentProducts:
    """Produtos genuinamente iguais — devem ter score ALTO."""

    def test_exact_same_name(self):
        result = match("Luva de Vinil P", "Luva de Vinil P")
        assert result.score >= 0.80
        assert result.tier == ConfidenceTier.HIGH

    def test_same_without_accents(self):
        result = match("Luva de Vinil Descartável", "Luva de Vinil Descartavel")
        assert result.score >= 0.75

    def test_abbreviation_vs_full(self):
        """S/ Pó = Sem Pó — produto equivalente."""
        result = match("Luva Vinil Sem Po P", "Luva Vinil S Po P")
        # "sem" e "s" são abreviações — similaridade parcial OK
        assert result.score >= 0.60

    def test_order_irrelevant(self):
        """A ordem das palavras não deveria importar para Jaccard."""
        result = match("Frasco Plastico 500ml", "500ml Frasco Plastico")
        assert result.score >= 0.70

    def test_uppercase_lowercase(self):
        result = match("LUVA VINIL P", "Luva vinil p")
        assert result.score >= 0.80

    def test_brand_added_in_ml(self):
        """ML geralmente adiciona marca que o catálogo não tem."""
        result = match("Luva de Vinil Descartavel P", "Luva de Vinil Descartavel P Volk")
        assert result.score >= 0.70
        assert result.is_usable

    def test_common_synonyms(self):
        """'Descartável' e 'Descartavel' são equivalentes após normalização."""
        result = match("Luva Descartavel Vinil", "Luva Descartavel Vinil")
        assert result.score >= 0.85

    def test_caixa_papelao(self):
        """Produto simples com dimensões."""
        result = match("Caixa de Papelao 30x20", "Caixa Papelao Ondulada 30x20")
        assert result.is_usable

    def test_papel_a4(self):
        result = match("Papel A4 75g Resma 500 Folhas", "Papel A4 75g m2 500 Folhas Resma")
        assert result.is_usable


# ── Match: produtos diferentes ────────────────────────────────────────────────

class TestDifferentProducts:
    """Produtos genuinamente diferentes — score deve ser BAIXO."""

    def test_completely_different(self):
        result = match("Luva de Vinil", "Sapato de Seguranca")
        assert result.score < 0.30

    def test_same_category_different_material(self):
        """Nitrilo vs Vinil — materiais diferentes, mesma categoria."""
        result = match("Luva de Vinil P", "Luva de Nitrilo P")
        # "vinil" e "nitrilo" são tokens de alto peso — penalidade na similaridade
        assert result.score < 0.65

    def test_completely_unrelated(self):
        result = match("Caneta Esferografica Azul", "Luva Vinil Descartavel P")
        assert result.score < 0.20


# ── Match: kits e combos ──────────────────────────────────────────────────────

class TestKitDetection:
    """Kits no ML quando catálogo tem item individual → penalidade alta."""

    def test_kit_in_ml_not_catalog(self):
        result = match("Luva de Vinil P", "Kit 100 Luvas Vinil P")
        assert result.score < 0.50
        assert "kit" in " ".join(result.reasons).lower()

    def test_combo_in_ml(self):
        result = match("Caneta Esferografica Azul", "Combo 10 Canetas Esferograficas Azul")
        assert result.score < 0.50

    def test_pack_in_ml(self):
        result = match("Frasco Plastico 500ml", "Pack 6 Frascos Plastico 500ml")
        assert result.score < 0.60

    def test_caixa_com_unidades(self):
        result = match("Luva Vinil P", "Caixa com 100 Luvas Vinil P")
        assert result.score < 0.55

    def test_kit_in_both_no_penalty(self):
        """Se catálogo também menciona kit — sem penalidade."""
        result = match("Kit Luvas Vinil P M G", "Kit Luvas Vinil P M G")
        assert result.score >= 0.75

    def test_individual_item_no_penalty(self):
        """Item individual sem menção a kit — sem penalidade."""
        result = match("Luva de Vinil P", "Luva de Vinil P Sem Po")
        assert result.score >= 0.70

    def test_n_unidades_in_ml(self):
        """'50 unidades' no ML = kit."""
        result = match("Sacola Kraft P", "Sacola Kraft P 50 unidades")
        assert result.score < 0.60


# ── Match: voltagem ───────────────────────────────────────────────────────────

class TestVoltageDetection:
    """Voltagem diferente = produto errado."""

    def test_110v_vs_220v(self):
        result = match("Secador Cabelo 1800W 110v", "Secador Cabelo 1800W 220v")
        assert result.score < 0.20
        assert "voltagem" in " ".join(result.reasons).lower()

    def test_127v_vs_220v(self):
        """127V é equivalente a 110V no Brasil."""
        result = match("Liquidificador 500W 127v", "Liquidificador 500W 220v")
        assert result.score < 0.20

    def test_bivolt_no_penalty(self):
        """Bivolt funciona nas duas tensões — sem penalidade."""
        result = match("Secador 1800W 110v", "Secador 1800W Bivolt")
        assert result.score >= 0.60
        assert "voltagem" not in " ".join(result.reasons).lower()

    def test_no_voltage_no_penalty(self):
        """Produto sem voltagem (ex: luva) — sem penalidade."""
        result = match("Luva de Vinil P", "Luva de Vinil P")
        assert result.score >= 0.80
        assert "voltagem" not in " ".join(result.reasons).lower()

    def test_same_voltage_no_penalty(self):
        result = match("Mixer 300W 110v", "Mixer 300W 110v")
        assert result.score >= 0.80

    def test_220v_vs_220v_no_penalty(self):
        result = match("Ventilador Mesa 220v", "Ventilador de Mesa 220v")
        assert result.score >= 0.70


# ── Match: tamanhos de roupa/EPI ──────────────────────────────────────────────

class TestSizeDetection:
    """Tamanho P vs G = variante errada."""

    def test_p_vs_g(self):
        result = match("Luva Nitrilo P", "Luva Nitrilo G")
        assert result.score < 0.45
        assert "tamanho" in " ".join(result.reasons).lower()

    def test_m_vs_gg(self):
        result = match("Avental Descartavel M", "Avental Descartavel GG")
        assert result.score < 0.50

    def test_same_size_no_penalty(self):
        result = match("Luva Vinil P", "Luva Vinil P Sem Po")
        assert result.score >= 0.65
        assert "tamanho" not in " ".join(result.reasons).lower()

    def test_no_size_in_catalog_no_penalty(self):
        """Catálogo não especifica tamanho — não penaliza se ML especifica."""
        result = match("Luva de Vinil Descartavel", "Luva de Vinil Descartavel P")
        assert result.score >= 0.65

    def test_xl_vs_m(self):
        result = match("Camiseta Seguranca XL", "Camiseta Seguranca M")
        assert result.score < 0.50


# ── Match: volume e peso ──────────────────────────────────────────────────────

class TestVolumeDetection:
    """Volume diferente (500ml vs 1L) = produto/embalagem diferente."""

    def test_500ml_vs_1l(self):
        result = match("Frasco Shampoo 500ml", "Frasco Shampoo 1 Litro")
        assert result.score < 0.55
        assert "volume" in " ".join(result.reasons).lower()

    def test_500ml_vs_500ml_no_penalty(self):
        result = match("Frasco Alcool 500ml", "Frasco Alcool Gel 500ml")
        assert result.score >= 0.60

    def test_1kg_vs_500g(self):
        result = match("Detergente Po 1kg", "Detergente Po 500g")
        assert result.score < 0.55

    def test_no_volume_no_penalty(self):
        """Produto sem volume — sem penalidade."""
        result = match("Luva Vinil P", "Luva Vinil P")
        assert result.score >= 0.80
        assert "volume" not in " ".join(result.reasons).lower()

    def test_equivalent_volumes(self):
        """1L = 1000ml — mesma quantidade, representação diferente.

        Nenhuma penalidade de volume aplicada (1000ml = 1L após conversão).
        Score MEDIUM (0.50) porque 'ml' e 'l' são tokens diferentes no Jaccard,
        mas is_usable=True e nenhuma penalidade é aplicada.
        """
        result = match("Frasco Alcool 1000ml", "Frasco Alcool 1 Litro")
        # sem penalidade de volume — tokens diferentes mas volume equivalente
        assert result.score >= 0.50
        assert "volume" not in " ".join(result.reasons).lower()
        assert result.is_usable


# ── Match: unidades incompatíveis ─────────────────────────────────────────────

class TestUnitMismatch:
    """Unidades de medida incompatíveis (kg vs L) = produtos diferentes."""

    def test_kg_vs_litros(self):
        result = match("Produto A 1kg", "Produto A 1 Litro")
        assert result.score < 0.60

    def test_same_unit_family_ok(self):
        """ml e L são da mesma família — comparação por volume, não penalidade de unidade."""
        result = match("Produto 500ml", "Produto 0.5 Litros")
        # Mesma unidade de volume — sem penalidade de grupo
        assert "unidade" not in " ".join(result.reasons).lower()


# ── Construção de query ───────────────────────────────────────────────────────

class TestBuildSearchQuery:
    """Query de busca otimizada para o ML."""

    def test_simple_name(self):
        q = build_search_query("Luva de Vinil P")
        assert "luva" in q
        assert "vinil" in q

    def test_removes_quantity_tokens(self):
        """'100 unid' é uma quantidade — não deve estar na query."""
        q = build_search_query("Luva Vinil P 100 unid")
        assert "100" not in q or "unid" not in q

    def test_limits_to_6_tokens(self):
        long_name = "Luva Vinil Descartavel Sem Po Powder Free Tamanho P Caixa"
        q = build_search_query(long_name)
        tokens = q.split()
        assert len(tokens) <= 6

    def test_preserves_dimensions(self):
        """Dimensões como A4, 500ml devem ficar na query."""
        q = build_search_query("Papel A4 75g Resma 500 Folhas")
        assert "a4" in q or "A4" in q.upper()

    def test_fallback_for_short_name(self):
        q = build_search_query("Luva")
        assert q == "luva"

    def test_empty_name(self):
        q = build_search_query("")
        # Não deve levantar exceção
        assert isinstance(q, str)


# ── Cenários realistas end-to-end ─────────────────────────────────────────────

class TestRealisticScenarios:
    """Cenários completos que reproduzem situações reais de catálogo vs ML."""

    def test_epi_standard(self):
        """EPI básico — match direto."""
        result = match("Luva de Vinil Descartavel Sem Po P", "Luva Vinil S/Po P 100 Unidades")
        # Kit de 100 → penalidade
        assert result.score < 0.60

    def test_epi_individual_vs_kit(self):
        result = match("Mascara Descartavel TNT", "Mascara Descartavel TNT Kit 50 unidades")
        assert result.score < 0.55

    def test_embalagem_match(self):
        result = match("Caixa Papelao Ondulada 30x20x15", "Caixa de Papelao Ondulada 30x20x15 cm")
        assert result.is_usable

    def test_eletrodomestico_bivolt(self):
        """Produto elétrico bivolt vs 110V — deve funcionar."""
        result = match("Batedeira 300W 110v", "Batedeira 300W Bivolt")
        assert result.is_usable

    def test_eletrodomestico_wrong_voltage(self):
        result = match("Batedeira 300W 110v", "Batedeira 300W 220v")
        assert not result.is_usable

    def test_liquido_volume_correto(self):
        result = match("Alcool Isopropilico 1 Litro", "Alcool Isopropilico 1L 99.8%")
        assert result.is_usable

    def test_liquido_volume_errado(self):
        result = match("Alcool Isopropilico 500ml", "Alcool Isopropilico 1 Litro")
        assert not result.is_usable

    def test_caneta_simples(self):
        result = match("Caneta Esferografica Azul", "Caneta Esferografica Ponta Media Azul BIC")
        assert result.is_usable

    def test_caneta_vs_lapis(self):
        result = match("Caneta Esferografica Azul", "Lapis Grafite HB")
        assert not result.is_usable

    def test_similar_but_different_material(self):
        """Luva vinil vs nitrilo — materiais diferentes, mesma categoria."""
        result = match("Luva de Vinil P", "Luva de Nitrilo P")
        # Alto peso de "vinil" e "nitrilo" — baixa intersecção → baixo score
        assert result.score < 0.65

    def test_ambiguous_produto_higiene(self):
        """Produto de higiene com nome genérico — match médio esperado."""
        result = match("Papel Toalha Interfolhado", "Papel Toalha Interfolhado 2 Folhas")
        # Sem penalidades claras, similaridade alta
        assert result.is_usable

    def test_produto_com_nfe_cnpj_no_titulo(self):
        """Alguns sellers colocam info de NF no título — deve ignorar."""
        result = match("Luva Vinil P", "Luva Vinil P c/ NF Emissao Rapida")
        assert result.is_usable

    def test_confidence_tier_high(self):
        result = match("Luva Vinil P", "Luva Vinil P")
        assert result.tier == ConfidenceTier.HIGH

    def test_confidence_tier_low(self):
        result = match("Luva Vinil P", "Furadeira Elétrica 1000W 110v")
        assert result.tier == ConfidenceTier.LOW

    def test_result_is_usable_medium(self):
        """Match médio deve ser usável."""
        result = match("Luva de Vinil Descartavel P", "Luva Vinil P Sem Po Powder Free Volk")
        if result.score >= 0.60:
            assert result.is_usable
        else:
            assert not result.is_usable
