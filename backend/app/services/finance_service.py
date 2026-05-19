"""
Finance Service — Calculo de viabilidade financeira de produtos.

FORMULAS MVP:
    ml_fee           = avg_price * (ml_fee_pct / 100)
    gross_margin     = avg_price - cost - ml_fee
    gross_margin_pct = (gross_margin / avg_price) * 100
    break_even_price = cost / (1 - ml_fee_pct / 100)
    price_safety_pct = (avg_price - break_even_price) / break_even_price * 100
    is_viable        = gross_margin > 0

EXTENSIBILIDADE (Fase 2):
    FeeConfig tem slots para: ADS, devolucao, embalagem, fulfillment, imposto.
    Todos zerados no MVP -- o calculo de net_margin os usa quando preenchidos.
    from_category() permite taxas diferentes por categoria (stub MVP).

PRECISAO:
    Todos os calculos usam Decimal para evitar erros de ponto flutuante.
    Dinheiro sempre arredondado em ROUND_HALF_UP com 2 casas decimais.
"""

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models.product import Product
from app.repositories.opportunity_repo import FinancialAnalysisRepository

logger = logging.getLogger(__name__)

_D2 = Decimal("0.01")
_HUNDRED = Decimal("100")
_ONE = Decimal("1")
_ZERO = Decimal("0")


# Fee Configuration

@dataclass(frozen=True)
class FeeConfig:
    """
    Configuracao de taxas e custos para calculo de margem.

    MVP: apenas ml_fee_pct impacta o resultado.
    Fase 2: demais campos sao ativados sem alterar a interface do calculo.
    """

    ml_fee_pct: Decimal = Decimal("15.00")
    ads_pct: Decimal = Decimal("0.00")
    return_rate_pct: Decimal = Decimal("0.00")
    packaging_cost_brl: Decimal = Decimal("0.00")
    fulfillment_cost_brl: Decimal = Decimal("0.00")
    tax_pct: Decimal = Decimal("0.00")

    @classmethod
    def default(cls) -> "FeeConfig":
        return cls()

    @classmethod
    def from_settings(cls) -> "FeeConfig":
        from app.core.config import settings
        return cls(ml_fee_pct=Decimal(str(settings.ML_FEE_PCT)))

    @classmethod
    def from_category(cls, category: str | None) -> "FeeConfig":
        """[Fase 2] MVP: sempre retorna from_settings()."""
        return cls.from_settings()

    @property
    def total_variable_pct(self) -> Decimal:
        return self.ml_fee_pct + self.ads_pct + self.return_rate_pct + self.tax_pct

    @property
    def total_fixed_brl(self) -> Decimal:
        return self.packaging_cost_brl + self.fulfillment_cost_brl

    def has_phase2_costs(self) -> bool:
        return (
            self.ads_pct > _ZERO
            or self.return_rate_pct > _ZERO
            or self.packaging_cost_brl > _ZERO
            or self.fulfillment_cost_brl > _ZERO
            or self.tax_pct > _ZERO
        )


# Financial Result

@dataclass
class FinancialResult:
    """Resultado de uma analise financeira para um produto."""

    cost: Decimal
    avg_market_price: Decimal
    fee_config: FeeConfig

    ml_fee: Decimal = field(init=False)
    gross_revenue: Decimal = field(init=False)
    gross_margin: Decimal = field(init=False)
    gross_margin_pct: Decimal = field(init=False)
    break_even_price: Decimal = field(init=False)
    price_safety_margin_pct: Decimal = field(init=False)
    is_viable: bool = field(init=False)
    net_margin: Decimal | None = field(init=False, default=None)
    net_margin_pct: Decimal | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._compute()

    def _compute(self) -> None:
        price = self.avg_market_price
        cost = self.cost
        cfg = self.fee_config

        self.ml_fee = _round2(price * cfg.ml_fee_pct / _HUNDRED)
        self.gross_revenue = price
        self.gross_margin = _round2(price - cost - self.ml_fee)
        self.gross_margin_pct = _round2(self.gross_margin / price * _HUNDRED)

        fee_rate = cfg.ml_fee_pct / _HUNDRED
        if fee_rate >= _ONE:
            self.break_even_price = price
        else:
            self.break_even_price = _round2(cost / (_ONE - fee_rate))

        if self.break_even_price > _ZERO:
            self.price_safety_margin_pct = _round2(
                (price - self.break_even_price) / self.break_even_price * _HUNDRED
            )
        else:
            self.price_safety_margin_pct = _ZERO

        self.is_viable = self.gross_margin > _ZERO

        if cfg.has_phase2_costs():
            ads = _round2(price * cfg.ads_pct / _HUNDRED)
            return_cost = _round2(cost * cfg.return_rate_pct / _HUNDRED)
            fixed_costs = cfg.total_fixed_brl
            tax = _round2(price * cfg.tax_pct / _HUNDRED)
            self.net_margin = _round2(self.gross_margin - ads - return_cost - fixed_costs - tax)
            self.net_margin_pct = _round2(self.net_margin / price * _HUNDRED) if price > _ZERO else _ZERO

    def to_db_dict(self) -> dict:
        return {
            "cost": self.cost,
            "avg_market_price": self.avg_market_price,
            "marketplace_fee_pct": self.fee_config.ml_fee_pct,
            "gross_revenue": self.gross_revenue,
            "ml_fee": self.ml_fee,
            "gross_margin": self.gross_margin,
            "gross_margin_pct": self.gross_margin_pct,
            "break_even_price": self.break_even_price,
            "price_safety_margin_pct": self.price_safety_margin_pct,
            "is_viable": self.is_viable,
            "ads_cost": _round2(self.avg_market_price * self.fee_config.ads_pct / _HUNDRED)
                if self.fee_config.ads_pct > _ZERO else None,
            "return_rate": self.fee_config.return_rate_pct / _HUNDRED
                if self.fee_config.return_rate_pct > _ZERO else None,
            "packaging_cost": self.fee_config.packaging_cost_brl
                if self.fee_config.packaging_cost_brl > _ZERO else None,
            "fulfillment_cost": self.fee_config.fulfillment_cost_brl
                if self.fee_config.fulfillment_cost_brl > _ZERO else None,
            "tax_cost": _round2(self.avg_market_price * self.fee_config.tax_pct / _HUNDRED)
                if self.fee_config.tax_pct > _ZERO else None,
            "net_margin": self.net_margin,
            "net_margin_pct": self.net_margin_pct,
        }

    def summary(self) -> str:
        status = "VIÁVEL" if self.is_viable else "NÃO VIÁVEL"
        return (
            f"{status} | custo=R${self.cost} preco=R${self.avg_market_price} "
            f"taxa=R${self.ml_fee} margem={self.gross_margin_pct}% "
            f"break_even=R${self.break_even_price}"
        )


# Funcao pura de calculo

def calculate(
    cost: Decimal,
    avg_market_price: Decimal,
    fee_config: FeeConfig | None = None,
) -> FinancialResult:
    """Calcula a viabilidade financeira de um produto. Funcao pura, sem DB."""
    if fee_config is None:
        fee_config = FeeConfig.from_settings()
    return FinancialResult(cost=cost, avg_market_price=avg_market_price, fee_config=fee_config)


# Orquestracao com banco de dados

def analyze_catalog(db: Session, products: list[Product]) -> int:
    """
    Calcula analise financeira para todos os produtos com dados de mercado.
    Produtos sem MarketAnalysis sao ignorados.
    Returns: numero de produtos analisados com sucesso.
    """
    repo = FinancialAnalysisRepository(db)
    analyzed = 0

    for product in products:
        if product.market_analysis is None:
            logger.info("Finance: SKIP '%s' — sem market_analysis no DB", product.search_name)
            continue

        try:
            result = calculate(
                cost=product.cost,
                avg_market_price=product.market_analysis.avg_price,
                fee_config=FeeConfig.from_category(product.category),
            )
            repo.upsert(product_id=product.id, **result.to_db_dict())
            analyzed += 1
            logger.info("Finance: '%s' | %s", product.search_name[:40], result.summary())

        except Exception as exc:
            logger.error(
                "Finance: erro '%s' (%s): %s",
                product.search_name, product.id, exc, exc_info=True,
            )

    logger.info("Finance: %d/%d produtos analisados", analyzed, len(products))
    return analyzed


# Utilitarios

def _round2(value: Decimal) -> Decimal:
    return value.quantize(_D2, rounding=ROUND_HALF_UP)
