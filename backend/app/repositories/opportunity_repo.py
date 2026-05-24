import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.analysis import (
    FinancialAnalysis,
    MarketAnalysis,
    OpportunityScore,
    Recommendation,
)
from app.models.listing import MarketListing
from app.repositories.base import BaseRepository


class MarketAnalysisRepository(BaseRepository[MarketAnalysis]):
    def __init__(self, db: Session) -> None:
        super().__init__(MarketAnalysis, db)

    def get_by_product(self, product_id: uuid.UUID) -> MarketAnalysis | None:
        return self.db.query(MarketAnalysis).filter(
            MarketAnalysis.product_id == product_id
        ).first()

    def upsert(self, product_id: uuid.UUID, **data) -> MarketAnalysis:
        """Cria ou atualiza análise de mercado para um produto."""
        existing = self.get_by_product(product_id)
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            return self.save(existing)
        analysis = MarketAnalysis(product_id=product_id, **data)
        return self.save(analysis)


class FinancialAnalysisRepository(BaseRepository[FinancialAnalysis]):
    def __init__(self, db: Session) -> None:
        super().__init__(FinancialAnalysis, db)

    def get_by_product(self, product_id: uuid.UUID) -> FinancialAnalysis | None:
        return self.db.query(FinancialAnalysis).filter(
            FinancialAnalysis.product_id == product_id
        ).first()

    def upsert(self, product_id: uuid.UUID, **data) -> FinancialAnalysis:
        existing = self.get_by_product(product_id)
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            return self.save(existing)
        analysis = FinancialAnalysis(product_id=product_id, **data)
        return self.save(analysis)


class MarketListingRepository(BaseRepository[MarketListing]):
    def __init__(self, db: Session) -> None:
        super().__init__(MarketListing, db)

    def replace_listings(
        self,
        product_id: uuid.UUID,
        listings: list[dict],
    ) -> list[MarketListing]:
        """
        Substitui todos os listings existentes de um produto pelos novos.

        Cada dict em `listings` deve conter:
            rank_position, item_id, title, price, sold_quantity (opt),
            permalink (opt), thumbnail (opt), match_confidence (opt)

        Deleta primeiro para garantir consistência sem lidar com
        conflitos de índice único (product_id, rank_position).
        """
        # Deletar listings antigos do produto
        self.db.query(MarketListing).filter(
            MarketListing.product_id == product_id
        ).delete(synchronize_session=False)

        # Inserir novos
        saved = []
        for data in listings:
            listing = MarketListing(product_id=product_id, **data)
            self.db.add(listing)
            saved.append(listing)

        # Commit explícito — garante que o último produto da fila também persiste.
        # Usar flush() dependia de um commit futuro (próximo repo.upsert), mas se o
        # último produto não tiver um próximo, os listings ficavam perdidos na transação.
        self.db.commit()
        return saved

    def get_by_product(self, product_id: uuid.UUID) -> list[MarketListing]:
        return (
            self.db.query(MarketListing)
            .filter(MarketListing.product_id == product_id)
            .order_by(MarketListing.rank_position.asc())
            .all()
        )


class OpportunityScoreRepository(BaseRepository[OpportunityScore]):
    def __init__(self, db: Session) -> None:
        super().__init__(OpportunityScore, db)

    def get_by_catalog_ranked(self, catalog_id: uuid.UUID) -> list[OpportunityScore]:
        """Retorna scores de um catálogo ordenados por rank."""
        from app.models.product import Product

        return (
            self.db.query(OpportunityScore)
            .join(Product, OpportunityScore.product_id == Product.id)
            .filter(Product.catalog_id == catalog_id)
            .order_by(OpportunityScore.rank.asc())
            .all()
        )

    def upsert(self, product_id: uuid.UUID, **data) -> OpportunityScore:
        existing = self.db.query(OpportunityScore).filter(
            OpportunityScore.product_id == product_id
        ).first()
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            return self.save(existing)
        score = OpportunityScore(product_id=product_id, **data)
        return self.save(score)
