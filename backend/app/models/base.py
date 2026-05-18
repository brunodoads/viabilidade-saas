import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Base declarativa única para todos os models.
    Todos os models devem herdar desta classe.
    """
    pass


class UUIDPrimaryKeyMixin:
    """Mixin que adiciona UUID como chave primária gerado no Python."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        sort_order=-2,  # Sempre primeira coluna
    )


class TimestampMixin:
    """Mixin que adiciona created_at e updated_at automáticos."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        sort_order=98,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        sort_order=99,
    )
