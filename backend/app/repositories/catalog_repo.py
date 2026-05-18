import uuid

from sqlalchemy.orm import Session

from app.models.catalog import Catalog, CatalogStatus, FileType
from app.repositories.base import BaseRepository


class CatalogRepository(BaseRepository[Catalog]):
    def __init__(self, db: Session) -> None:
        super().__init__(Catalog, db)

    def get_by_user(self, user_id: uuid.UUID, limit: int = 50) -> list[Catalog]:
        return (
            self.db.query(Catalog)
            .filter(Catalog.user_id == user_id)
            .order_by(Catalog.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_id_and_user(self, catalog_id: uuid.UUID, user_id: uuid.UUID) -> Catalog | None:
        """Busca catálogo garantindo que pertence ao usuário (segurança MVP)."""
        return (
            self.db.query(Catalog)
            .filter(Catalog.id == catalog_id, Catalog.user_id == user_id)
            .first()
        )

    def create_catalog(
        self,
        user_id: uuid.UUID,
        original_filename: str,
        file_path: str,
        file_type: FileType,
    ) -> Catalog:
        catalog = Catalog(
            user_id=user_id,
            original_filename=original_filename,
            file_path=file_path,
            file_type=file_type,
            status=CatalogStatus.PENDING,
        )
        return self.save(catalog)

    def update_status(
        self,
        catalog: Catalog,
        status: CatalogStatus,
        error_message: str | None = None,
    ) -> Catalog:
        catalog.status = status
        if error_message is not None:
            catalog.error_message = error_message
        return self.save(catalog)

    def update_progress(
        self,
        catalog: Catalog,
        total_products: int,
        processed_products: int,
    ) -> Catalog:
        catalog.total_products = total_products
        catalog.processed_products = processed_products
        return self.save(catalog)
