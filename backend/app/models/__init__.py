# Importar todos os models aqui garante que o Alembic os descubra
# para autogenerate de migrations.
from app.models.base import Base  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.catalog import Catalog, CatalogStatus, FileType  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.analysis import (  # noqa: F401
    FinancialAnalysis,
    MarketAnalysis,
    OpportunityScore,
    Recommendation,
)

__all__ = [
    "Base",
    "User",
    "Catalog",
    "CatalogStatus",
    "FileType",
    "Product",
    "MarketAnalysis",
    "FinancialAnalysis",
    "OpportunityScore",
    "Recommendation",
]
