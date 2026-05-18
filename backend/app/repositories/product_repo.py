import uuid
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models.product import Product
from app.repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    def __init__(self, db: Session) -> None:
        super().__init__(Product, db)

    def get_by_catalog(self, catalog_id: uuid.UUID) -> list[Product]:
        return (
            self.db.query(Product)
            .filter(Product.catalog_id == catalog_id)
            .all()
        )

    def get_by_catalog_with_analyses(self, catalog_id: uuid.UUID) -> list[Product]:
        """Carrega produtos com todas as análises em um único query (eager loading)."""
        return (
            self.db.query(Product)
            .options(
                joinedload(Product.market_analysis),
                joinedload(Product.financial_analysis),
                joinedload(Product.opportunity_score),
            )
            .filter(Product.catalog_id == catalog_id)
            .all()
        )

    def create_product(
        self,
        catalog_id: uuid.UUID,
        user_id: uuid.UUID,
        raw_name: str,
        cost: Decimal,
        normalized_name: str | None = None,
        sku: str | None = None,
        category: str | None = None,
        supplier: str | None = None,
        currency: str = "BRL",
    ) -> Product:
        product = Product(
            catalog_id=catalog_id,
            user_id=user_id,
            raw_name=raw_name,
            normalized_name=normalized_name,
            sku=sku,
            category=category,
            supplier=supplier,
            cost=cost,
            currency=currency,
        )
        return self.save(product)

    def bulk_create(self, products: list[Product]) -> list[Product]:
        """Insere múltiplos produtos em batch — mais eficiente para catálogos grandes."""
        self.db.add_all(products)
        self.db.commit()
        for p in products:
            self.db.refresh(p)
        return products
