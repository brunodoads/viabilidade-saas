import uuid
from typing import Generic, Type, TypeVar

from sqlalchemy.orm import Session

from app.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Repository base com operações CRUD genéricas.

    Uso:
        class CatalogRepository(BaseRepository[Catalog]):
            def __init__(self, db: Session):
                super().__init__(Catalog, db)
    """

    def __init__(self, model: Type[ModelType], db: Session) -> None:
        self.model = model
        self.db = db

    def get_by_id(self, id: uuid.UUID) -> ModelType | None:
        return self.db.get(self.model, id)

    def get_all(self, limit: int = 100, offset: int = 0) -> list[ModelType]:
        return self.db.query(self.model).offset(offset).limit(limit).all()

    def save(self, instance: ModelType) -> ModelType:
        """Persiste uma instância nova ou existente."""
        self.db.add(instance)
        self.db.commit()
        self.db.refresh(instance)
        return instance

    def delete(self, instance: ModelType) -> None:
        self.db.delete(instance)
        self.db.commit()

    def flush(self, instance: ModelType) -> ModelType:
        """Envia ao banco sem commit — útil em transações compostas."""
        self.db.add(instance)
        self.db.flush()
        self.db.refresh(instance)
        return instance
