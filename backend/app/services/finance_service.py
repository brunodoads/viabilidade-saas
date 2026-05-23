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
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models.analysis import MarketAnalysis
from app.models.product import Product
from app.repositories.opportunity_repo import FinancialAnalysisRepository

logger = logging.getLogger(__name__)

# ── Extração de unidades por embalagem ───────────────────────────────────────
# Catálogos de distribuidoras trazem o tamanho do lote no nome:
#   "LEITE CONDENSADO MOCA 395G CX24" → 24 unidades por caixa
#   "SABAO PO OMO 1KG PCT12"          → 12 unidades por pacote
#   "OLEO SOJA CARGILL 900ML"         → 1 (sem código = unidade individual)
#
# O custo do catálogo é o custo do LOTE. Para comparar com o preço individual
# do ML (preço de varejo), calculamos: unit_cost = lote_cost / qty

_UNITS_PER_PKG_RE = re.compile(
    r"\bcx\s*c?/?\s*(\d+)\b"      # CX24, CX/24, CXC/24
    r"|\bfardo\s*(\d+)\b"          # FARDO30
    r"|\bfd\s*(\d+)\b"             # FD24
    r"|\bpct\s*(\d+)\b"            # PCT12
    r"|\bpack\s*(\d+)\b"           # PACK6
    r"|\bfco\s*(\d+)\b"            # FCO6 (frasco)
    r"|\bc/\s*(\d+)\b"             # C/24
    r"|\b(\d{2,3})\s*x\s*\d+\b",  # 12X1 (12 caixas de 1 un) — ignora 2x3 etc
    re.IGNORECASE,
)


def extract_units_per_package(product_name: str) -> int:
    """
    Extrai a quantidade de unidades por embalagem do nome do produto.

    Returns:
        Número de unidades (mínimo 1). 1 = produto vendido individualmente.
    """
    match = _UNITS_PER_PKG_RE.search(product_name)
    if match:
        for group in match.groups():
            if group is not None:
                return max(1, int(group))
    return 1

_D2 = Decimal("0.01")
_HUNDRED = Decimal("100")
_ONE = Decimal("1")
_ZERO = Decimal("0")
_MAX_SAFETY_PCT = Decimal("999.99")  # Numeric(5,2) DB limit — evita DataError em produtos baratos com preço ML alto


# Fee Configuration

@dataclass(frozen=True)
class FeeConfig:
    """
    Configuracao de taxas e custos para calculo de margem.

    MVP: ml_fee_pct + fulfillment_cost_brl + tax_pct + min_viable_margin_pct
    Fase 2: ads_pct, return_rate_pct, packaging_cost_brl
    """

    ml_fee_pct: Decimal = Decimal("15.00")
    ads_pct: Decimal = Decimal("0.00")
    return_rate_pct: Decimal = Decimal("0.00")
    packaging_cost_brl: Decimal = Decimal("0.00")
    fulfillment_cost_brl: Decimal = Decimal("0.00")
    tax_pct: Decimal = Decimal("0.00")
    # Margem líquida mínima para produto ser VIÁVEL (%).
    # Padrão 20% = mínimo recomendado para e-commerce sustentável.
    min_viable_margin_pct: Decimal = Decimal("20.00")

    @classmethod
    def default(cls) -> "FeeConfig":
        return cls()

    @classmethod
    def from_settings(cls) -> "FeeConfig":
        from app.core.config import settings
        return cls(
            ml_fee_pct=Decimal(str(settings.ML_FEE_PCT)),
            # Frete via Mercado Envios — obrigatório para frete grátis
            fulfillment_cost_brl=Decimal(str(settings.ML_SHIPPING_COST_BRL)),
            # Imposto: Simples Nacional sobre receita bruta
            tax_pct=Decimal(str(settings.ML_TAX_PCT)),
            # Margem líquida mínima para is_viable
            min_viable_margin_pct=Decimal(str(settings.ML_MIN_VIABLE_MARGIN_PCT)),
        )


    @classmethod
    def from_market_analysis(cls, market_analysis: "MarketAnalysis") -> "FeeConfig":
        """
        Cria FeeConfig com taxa real do mercado.

        Taxa: usa avg_ml_fee_pct da analise (Listing Prices API); fallback = settings.
        Frete: se >50% anuncios com frete gratis -> seller absorve (R$20); senao R$0.
        """
        from app.core.config import settings
        ml_fee_pct = (
            market_analysis.avg_ml_fee_pct
            if market_analysis.avg_ml_fee_pct is not None
            else Decimal(str(settings.ML_FEE_PCT))
        )
        free_pct = market_analysis.free_shipping_pct
        if free_pct is not None:
            shipping = Decimal(str(settings.ML_SHIPPING_COST_BRL)) if free_pct > Decimal("50") else Decimal("0")
        else:
            shipping = Decimal(str(settings.ML_SHIPPING_COST_BRL))
        return cls(
            ml_fee_pct=ml_fee_pct,
            fulfillment_cost_brl=shipping,
            tax_pct=Decimal(str(settings.ML_TAX_PCT)),
            min_viable_margin_pct=Decimal(str(settings.ML_MIN_VIABLE_MARGIN_PCT)),
        )

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
    # Preço mínimo de venda para atingir a margem líquida alvo (padrão 20%)
    min_price_for_target_margin: Decimal | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._compute()

    def _compute(self) -> None:
        price = self.avg_market_price
        cost = self.cost
        cfg = self.fee_config

        # Guard: avg_price=0 ocorre quando Apify retorna dados inválidos.
        # Produto inviável por definição — evita ZeroDivisionError no cálculo de margens.
        if price <= _ZERO:
            self.ml_fee = _ZERO
            self.gross_revenue = _ZERO
            self.gross_margin = _round2(-cost)
            self.gross_margin_pct = _ZERO
            self.break_even_price = cost
            self.price_safety_margin_pct = _ZERO
            self.is_viable = False
            return

        # ── Margem BRUTA (sem frete/imposto) ────────────────────────────────
        self.ml_fee = _round2(price * cfg.ml_fee_pct / _HUNDRED)
        self.gross_revenue = price
        self.gross_margin = _round2(price - cost - self.ml_fee)
        self.gross_margin_pct = _round2(self.gross_margin / price * _HUNDRED)

        # Break-even: preço mínimo para cobrir custo + taxa ML
        fee_rate = cfg.ml_fee_pct / _HUNDRED
        if fee_rate >= _ONE:
            self.break_even_price = price
        else:
            self.break_even_price = _round2(cost / (_ONE - fee_rate))

        if self.break_even_price > _ZERO:
            raw_safety = _round2(
                (price - self.break_even_price) / self.break_even_price * _HUNDRED
            )
            # Clamp para o limite Numeric(10,2) do PostgreSQL.
            self.price_safety_margin_pct = min(raw_safety, _MAX_SAFETY_PCT)
        else:
            self.price_safety_margin_pct = _ZERO

        # ── Margem LÍQUIDA (com frete + imposto + ADS + devoluções) ─────────
        # has_phase2_costs() é True sempre que fulfillment_cost_brl > 0 ou tax_pct > 0,
        # ou seja: sempre quando FeeConfig.from_settings() é usado (frete=R$20, imposto=7%).
        if cfg.has_phase2_costs():
            ads = _round2(price * cfg.ads_pct / _HUNDRED)
            return_cost = _round2(cost * cfg.return_rate_pct / _HUNDRED)
            fixed_costs = cfg.total_fixed_brl      # frete + embalagem
            tax = _round2(price * cfg.tax_pct / _HUNDRED)
            self.net_margin = _round2(self.gross_margin - ads - return_cost - fixed_costs - tax)
            self.net_margin_pct = _round2(self.net_margin / price * _HUNDRED)

        # ── is_viable: com custos reais, exige margem líquida >= target ─────
        # Sem custos reais (FeeConfig.default()), usa sinal simples gross_margin > 0.
        # Com custos reais (from_settings()), exige net_margin_pct >= min_viable_margin_pct.
        if cfg.has_phase2_costs() and self.net_margin is not None:
            self.is_viable = self.net_margin_pct >= cfg.min_viable_margin_pct
        else:
            self.is_viable = self.gross_margin > _ZERO

        # ── min_price_for_target_margin ──────────────────────────────────────
        # Preço mínimo de venda para atingir exatamente min_viable_margin_pct de margem líquida.
        #
        # Dedução:
        #   net_margin = price - cost - (ml_fee_pct/100)*price - (tax_pct/100)*price
        #              - (ads_pct/100)*price - shipping - return_cost
        #   net_margin_pct = net_margin / price
        #   Queremos: net_margin_pct = target_pct / 100
        #   → price * (1 - ml_fee_pct/100 - tax_pct/100 - ads_pct/100 - target_pct/100)
        #       = cost + shipping + return_cost
        #   → price = (cost + shipping) / (1 - variable_rates - target_rate)
        #   (return_cost ignorado: return_rate=0 no MVP)
        if cfg.has_phase2_costs():
            variable_rate = (cfg.ml_fee_pct + cfg.tax_pct + cfg.ads_pct) / _HUNDRED
            target_rate = cfg.min_viable_margin_pct / _HUNDRED
            denominator = _ONE - variable_rate - target_rate
            if denominator > _ZERO:
                self.min_price_for_target_margin = _round2(
                    (cost + cfg.total_fixed_brl) / denominator
                )
            # Se denominator <= 0: matematicamente impossível — deixa None

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
            "min_price_for_target_margin": self.min_price_for_target_margin,
        }

    def summary(self) -> str:
        status = "VIÁVEL" if self.is_viable else "NÃO VIÁVEL"
        net_info = (
            f" | líquida={self.net_margin_pct}% min_preco=R${self.min_price_for_target_margin}"
            if self.net_margin_pct is not None else ""
        )
        return (
            f"{status} | custo=R${self.cost} preco=R${self.avg_market_price} "
            f"taxa=R${self.ml_fee} bruta={self.gross_margin_pct}%"
            f"{net_info}"
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

    # PRÉ-FILTRAR antes de qualquer db.commit().
    # SQLAlchemy com expire_on_commit=True expira TODOS os objetos após cada commit.
    # Se iterar sobre todos os 403 produtos APÓS o primeiro commit, cada acesso a
    # product.market_analysis dispara um SELECT individual (N+1 queries).
    # Com 403 produtos × ~200ms Railway = ~80s de overhead desnecessário.
    # Pré-filtrar antes dos commits garante que verificamos os atributos ainda em memória.
    products_to_analyze = [p for p in products if p.market_analysis is not None]

    if not products_to_analyze:
        logger.info("Finance: nenhum produto com market_analysis — nada a analisar")
        return 0

    logger.info("Finance: %d/%d produtos com dados de mercado", len(products_to_analyze), len(products))

    for product in products_to_analyze:
        try:
            # Calcular custo unitário real.
            # Catálogos de distribuidoras listam o custo do LOTE (ex: CX24 = 24 unidades).
            # O ML vende por unidade, então comparamos o custo unitário.
            # Sem código de embalagem → units_per_package = 1 → unit_cost = product.cost
            #
            # ATENÇÃO: após db.commit(), SQLAlchemy expira os objetos (expire_on_commit=True).
            # Acessar product.market_analysis aqui pode triggerar lazy load — mas como
            # iteramos apenas sobre products_to_analyze (pré-filtrado), são no máximo
            # 20 lazy loads × ~200ms = ~4s em vez de 403 × 200ms = ~80s.
            units = extract_units_per_package(product.raw_name)
            unit_cost = _round2(Decimal(str(product.cost)) / Decimal(str(units)))

            if units > 1:
                logger.info(
                    "Finance: '%s' → %d unid/embalagem | custo_lote=R$%.2f | custo_unit=R$%.2f",
                    product.search_name[:40], units, float(product.cost), float(unit_cost)
                )

            result = calculate(
                cost=unit_cost,
                avg_market_price=product.market_analysis.avg_price,
                fee_config=FeeConfig.from_market_analysis(product.market_analysis),
            )
            repo.upsert(product_id=product.id, **result.to_db_dict())
            analyzed += 1
            logger.info("Finance: '%s' | %s", product.search_name[:40], result.summary())

        except Exception as exc:
            logger.error(
                "Finance: erro '%s' (%s): %s",
                product.search_name, product.id, exc, exc_info=True,
            )
            # CRÍTICO: após qualquer falha de commit (ex: DataError por overflow),
            # a sessão SQLAlchemy entra em estado PendingRollbackError.
            # Sem rollback explícito, TODOS os produtos seguintes falham também.
            try:
                db.rollback()
            except Exception:
                pass

    logger.info("Finance: %d/%d produtos analisados", analyzed, len(products))
    return analyzed


# Utilitarios

def _round2(value: Decimal) -> Decimal:
    return value.quantize(_D2, rounding=ROUND_HALF_UP)

