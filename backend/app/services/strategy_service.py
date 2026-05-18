"""
Strategy Service — Scoring estratégico e ranking de oportunidades comerciais.

OBJETIVO:
    Transformar dados financeiros e de mercado em um score único (0-100) que
    representa a atratividade comercial de um produto para revenda no ML.

ALGORITMO MVP (pesos fixos, soma = 1.0):
    demand_score      = demanda comprovada em volume de vendas    — peso 35%
    margin_score      = margem bruta disponível para o revendedor — peso 40%
    competition_score = saturação do mercado (menos = melhor)     — peso 15%
    confidence_score  = qualidade dos dados de matching ML        — peso 10%

    final_score = (demand * 0.35) + (margin * 0.40) + (competition * 0.15) + (confidence * 0.10)

CLASSIFICAÇÃO:
    >= 75 → EXCELENTE  — agir com prioridade
    >= 55 → BOA        — analisar e considerar
    >= 35 → ARRISCADA  — cautela, margem ou demanda limitada
    <  35 → EVITAR     — não recomendado no momento

PRINCÍPIO DE DESIGN:
    Cada subscore é calculado por uma função pura (sem DB) → testável isolado.
    O orquestrador (score_catalog) é a única função que toca o banco.

Fase 2:
    - Pesos configuráveis por organização (organizations.score_weights)
    - Subscore de reputação de vendedores
    - Tendência de demanda (crescimento/queda dos últimos 30 dias)
"""

import logging
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models.analysis import Recommendation
from app.models.product import Product
from app.repositories.opportunity_repo import OpportunityScoreRepository

logger = logging.getLogger(__name__)

# ── Pesos (soma deve ser exatamente 1.0) ─────────────────────────────────────

WEIGHT_DEMAND = Decimal("0.35")
WEIGHT_MARGIN = Decimal("0.40")
WEIGHT_COMPETITION = Decimal("0.15")
WEIGHT_CONFIDENCE = Decimal("0.10")

assert WEIGHT_DEMAND + WEIGHT_MARGIN + WEIGHT_COMPETITION + WEIGHT_CONFIDENCE == Decimal("1.00"), \
    "Pesos do score devem somar exatamente 1.0"

# ── Thresholds de classificação ───────────────────────────────────────────────

THRESHOLD_EXCELENTE = Decimal("75")
THRESHOLD_BOA = Decimal("55")
THRESHOLD_ARRISCADA = Decimal("35")

# ── Parâmetros de normalização ────────────────────────────────────────────────

_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

# Demand: avg_sold_quantity por faixa → score
_DEMAND_TIERS = [
    (50_000, Decimal("100")),
    (20_000, Decimal("85")),
    (10_000, Decimal("70")),
    (5_000, Decimal("55")),
    (2_000, Decimal("38")),
    (1_000, Decimal("20")),   # mínimo configurável (MIN_SALES_THRESHOLD)
    (0,     Decimal("5")),    # menos de 1000 — abaixo do threshold padrão
]

# Demand fallback: listings_above_threshold (quando avg_sold_quantity não disponível)
_MAX_DEMAND_LISTINGS = 50  # >= 50 anúncios com +1k vendas = demand score 100

# Margin: gross_margin_pct por faixa → score
_MARGIN_TIERS = [
    (Decimal("40"), Decimal("100")),
    (Decimal("30"), Decimal("80")),
    (Decimal("20"), Decimal("60")),
    (Decimal("10"), Decimal("35")),
    (Decimal("0"),  Decimal("10")),   # acima de zero mas abaixo de 10%
    # negativo → score 0 (tratado antes das tiers)
]

# Competition: total_sellers por faixa → score (menos vendedores = mais alto)
_COMPETITION_TIERS = [
    (3,  Decimal("100")),  # até 3 vendedores = mercado virgem
    (8,  Decimal("90")),   # até 8 vendedores
    (15, Decimal("75")),
    (25, Decimal("55")),
    (40, Decimal("35")),
    (60, Decimal("18")),   # 41-60 vendedores = saturado
    # acima de 60 → score 5 (mercado extremamente saturado)
]
_COMPETITION_FLOOR = Decimal("5")


# ── Filtro de oportunidades ───────────────────────────────────────────────────

@dataclass
class OpportunityFilter:
    """
    Critérios para filtrar oportunidades do ranking.

    Exemplo de uso:
        f = OpportunityFilter(min_final_score=55, only_viable=True)
        good_ones = [o for o in opportunities if f.matches(o)]
    """

    min_final_score: float = 0.0
    """Score mínimo (0-100). Ex: 55 = apenas BOA ou EXCELENTE."""

    min_margin_pct: float = 0.0
    """Margem bruta mínima em %. Ex: 20 = mínimo 20% de margem."""

    min_demand_listings: int = 0
    """Mínimo de anúncios com +1k vendas encontrados."""

    recommendations: list[Recommendation] | None = None
    """Filtrar por classificação específica. None = todas."""

    only_viable: bool = True
    """Excluir produtos com margem negativa (is_viable = False)."""

    @classmethod
    def only_excellent(cls) -> "OpportunityFilter":
        """Retorna apenas oportunidades EXCELENTE."""
        return cls(
            min_final_score=float(THRESHOLD_EXCELENTE),
            recommendations=[Recommendation.EXCELENTE],
        )

    @classmethod
    def good_and_above(cls, min_margin: float = 20.0) -> "OpportunityFilter":
        """BOA ou EXCELENTE, com margem mínima customizável."""
        return cls(
            min_final_score=float(THRESHOLD_BOA),
            min_margin_pct=min_margin,
            recommendations=[Recommendation.EXCELENTE, Recommendation.BOA],
        )

    def matches(self, product: "Product") -> bool:
        """
        Verifica se um produto (com relacionamentos carregados) passa no filtro.

        Args:
            product: Product com financial_analysis e opportunity_score carregados.
        """
        fa = product.financial_analysis
        ma = product.market_analysis
        sc = product.opportunity_score

        if sc is None or fa is None or ma is None:
            return False

        if self.only_viable and not fa.is_viable:
            return False

        if float(sc.final_score) < self.min_final_score:
            return False

        if float(fa.gross_margin_pct) < self.min_margin_pct:
            return False

        if ma.listings_above_threshold < self.min_demand_listings:
            return False

        if self.recommendations is not None and sc.recommendation not in self.recommendations:
            return False

        return True


# ── Resultado de scoring por produto ─────────────────────────────────────────

@dataclass
class ScoringResult:
    """Resultado do scoring para um produto individual."""

    demand_score: Decimal
    margin_score: Decimal
    competition_score: Decimal
    confidence_score: Decimal
    final_score: Decimal
    recommendation: Recommendation
    explanation: str

    def to_db_dict(self) -> dict:
        return {
            "demand_score": self.demand_score,
            "margin_score": self.margin_score,
            "competition_score": self.competition_score,
            "confidence_score": self.confidence_score,
            "final_score": self.final_score,
            "recommendation": self.recommendation,
            "explanation": self.explanation,
        }


# ── Cálculo de subscore por fator ─────────────────────────────────────────────

def demand_score(
    listings_above_threshold: int,
    avg_sold_quantity: int | None = None,
) -> Decimal:
    """
    Calcula o score de demanda (0–100).

    Sinal primário: avg_sold_quantity (média de vendas dos anúncios aprovados).
    Sinal fallback: listings_above_threshold (nº de anúncios com +1k vendas).

    O sinal primário é mais confiável porque reflete volume real de vendas,
    não apenas quantidade de anúncios.
    """
    if avg_sold_quantity is not None and avg_sold_quantity > 0:
        for threshold, score in _DEMAND_TIERS:
            if avg_sold_quantity >= threshold:
                return score
        return Decimal("5")

    # Fallback: normalização linear por nº de listings qualificados
    capped = min(listings_above_threshold, _MAX_DEMAND_LISTINGS)
    if _MAX_DEMAND_LISTINGS == 0:
        return _ZERO
    return _round2(Decimal(str(capped)) / Decimal(str(_MAX_DEMAND_LISTINGS)) * _HUNDRED)


def margin_score(gross_margin_pct: Decimal) -> Decimal:
    """
    Calcula o score de margem (0–100).

    Margem negativa → score 0 (produto inviável).
    Margem positiva → score proporcional à faixa.
    """
    if gross_margin_pct <= _ZERO:
        return _ZERO

    for threshold, score in _MARGIN_TIERS:
        if gross_margin_pct >= threshold:
            return score

    return _ZERO


def competition_score(total_sellers: int) -> Decimal:
    """
    Calcula o score de concorrência (0–100).

    Menos vendedores = score maior (mercado menos saturado).
    Zero vendedores = 100 (mercado virgem ou nicho).
    """
    for max_sellers, score in _COMPETITION_TIERS:
        if total_sellers <= max_sellers:
            return score
    return _COMPETITION_FLOOR


def confidence_score(avg_match_confidence: Decimal | float | None) -> Decimal:
    """
    Converte a confiança média do matching (0.0–1.0) em score (0–100).

    Quando não disponível, assume confiança máxima (100) — dados foram
    validados por outros meios ou o produto é muito específico.
    """
    if avg_match_confidence is None:
        return _HUNDRED

    value = Decimal(str(avg_match_confidence)) * _HUNDRED
    return _round2(max(_ZERO, min(_HUNDRED, value)))


# ── Score final ───────────────────────────────────────────────────────────────

def calculate_final_score(
    d_score: Decimal,
    m_score: Decimal,
    c_score: Decimal,
    conf_score: Decimal,
) -> Decimal:
    """Combina os subscores em um score final ponderado (0–100)."""
    raw = (
        d_score * WEIGHT_DEMAND
        + m_score * WEIGHT_MARGIN
        + c_score * WEIGHT_COMPETITION
        + conf_score * WEIGHT_CONFIDENCE
    )
    return _round2(max(_ZERO, min(_HUNDRED, raw)))


# ── Classificação ─────────────────────────────────────────────────────────────

def classify(score: Decimal) -> Recommendation:
    """
    Classifica o score final em recomendação qualitativa.

    >= 75: EXCELENTE
    >= 55: BOA
    >= 35: ARRISCADA
    <  35: EVITAR
    """
    if score >= THRESHOLD_EXCELENTE:
        return Recommendation.EXCELENTE
    elif score >= THRESHOLD_BOA:
        return Recommendation.BOA
    elif score >= THRESHOLD_ARRISCADA:
        return Recommendation.ARRISCADA
    else:
        return Recommendation.EVITAR


# ── Explicação textual ────────────────────────────────────────────────────────

def build_explanation(
    d_score: Decimal,
    m_score: Decimal,
    c_score: Decimal,
    conf_score: Decimal,
    is_viable: bool,
) -> str:
    """
    Gera uma explicação textual da oportunidade para o usuário final.

    Objetivo: ser claro e direto — o usuário precisa entender O QUE está
    impulsionando ou limitando a oportunidade, sem jargão técnico.

    Exemplos de output:
        "Alta demanda comprovada, boa margem e baixa concorrência."
        "Margem excelente, mas concorrência alta e demanda moderada."
        "Produto inviável — preço de mercado não cobre o custo de compra."
    """
    if not is_viable:
        return "Produto inviável — o preço médio de mercado não cobre o custo de compra."

    parts: list[str] = []

    # Demanda
    if d_score >= Decimal("70"):
        parts.append("alta demanda comprovada")
    elif d_score >= Decimal("40"):
        parts.append("demanda moderada")
    else:
        parts.append("demanda limitada")

    # Margem
    if m_score >= Decimal("80"):
        parts.append("margem excelente")
    elif m_score >= Decimal("60"):
        parts.append("boa margem")
    elif m_score >= Decimal("35"):
        parts.append("margem razoável")
    else:
        parts.append("margem baixa")

    # Concorrência
    if c_score >= Decimal("75"):
        parts.append("baixa concorrência")
    elif c_score >= Decimal("55"):
        parts.append("concorrência moderada")
    else:
        parts.append("alta concorrência")

    # Confiança — só menciona quando baixa (não é fator comercial primário)
    if conf_score < Decimal("60"):
        parts.append("dados de matching com baixa confiança — verificar manualmente")

    # Formatar: vírgulas + ponto final, capitalizar primeira letra
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


# ── Cálculo completo por produto (função pura) ────────────────────────────────

def score_product(product: Product) -> ScoringResult:
    """
    Calcula o ScoringResult completo para um produto.

    Requer que product.financial_analysis e product.market_analysis estejam carregados.

    Raises:
        ValueError: se financial_analysis ou market_analysis estiverem ausentes.
    """
    fa = product.financial_analysis
    ma = product.market_analysis

    if fa is None:
        raise ValueError(f"Produto {product.id} sem financial_analysis")
    if ma is None:
        raise ValueError(f"Produto {product.id} sem market_analysis")

    d = demand_score(
        listings_above_threshold=ma.listings_above_threshold,
        avg_sold_quantity=ma.avg_sold_quantity,
    )
    m = margin_score(fa.gross_margin_pct)
    c = competition_score(ma.total_sellers)
    conf = confidence_score(ma.avg_match_confidence)

    final = calculate_final_score(d, m, c, conf)
    rec = classify(final)
    expl = build_explanation(d, m, c, conf, fa.is_viable)

    return ScoringResult(
        demand_score=d,
        margin_score=m,
        competition_score=c,
        confidence_score=conf,
        final_score=final,
        recommendation=rec,
        explanation=expl,
    )


# ── Orquestração com banco de dados ──────────────────────────────────────────

def score_catalog(
    db: Session,
    catalog_id: uuid.UUID,
    products: list[Product],
) -> int:
    """
    Calcula scores e ranks para todos os produtos de um catálogo.

    Fluxo:
        1. Filtrar produtos com dados completos (financial + market)
        2. Calcular ScoringResult para cada produto
        3. Ordenar por final_score DESC → atribuir rank
        4. Persistir (upsert) em OpportunityScore

    Returns:
        Número de produtos pontuados
    """
    repo = OpportunityScoreRepository(db)

    scoreable = [
        p for p in products
        if p.financial_analysis is not None and p.market_analysis is not None
    ]

    if not scoreable:
        logger.warning(
            "Strategy: nenhum produto com dados completos para scoring "
            "(catalog_id=%s)", catalog_id
        )
        return 0

    # Calcular scores — erro por produto não interrompe o batch
    scored: list[tuple[Product, ScoringResult]] = []

    for product in scoreable:
        try:
            result = score_product(product)
            scored.append((product, result))
        except Exception as exc:
            logger.error(
                "Strategy: erro no scoring de '%s' (%s): %s",
                product.search_name, product.id, exc, exc_info=True,
            )

    # Ordenar por final_score DESC → rank crescente (1 = melhor)
    scored.sort(key=lambda x: float(x[1].final_score), reverse=True)

    # Persistir com rank calculado
    for rank, (product, result) in enumerate(scored, start=1):
        repo.upsert(
            product_id=product.id,
            rank=rank,
            **result.to_db_dict(),
        )
        logger.info(
            "Strategy: rank #%d | '%s' | score=%.1f | %s | %s",
            rank,
            product.search_name[:35],
            float(result.final_score),
            result.recommendation.value,
            result.explanation,
        )

    logger.info(
        "Strategy: concluído | %d produtos rankeados para catálogo %s",
        len(scored), catalog_id,
    )
    return len(scored)


def filter_opportunities(
    products: list[Product],
    opportunity_filter: OpportunityFilter,
) -> list[Product]:
    """
    Filtra e retorna apenas produtos que passam no filtro.

    Args:
        products: Lista de produtos com todos os relacionamentos carregados.
        opportunity_filter: Critérios de filtro.

    Returns:
        Produtos filtrados, mantendo a ordem original (presumivelmente por rank).
    """
    return [p for p in products if opportunity_filter.matches(p)]


# ── Utilitários ───────────────────────────────────────────────────────────────

def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
