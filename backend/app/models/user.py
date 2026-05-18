from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Usuário do sistema.

    MVP: um usuário = um tenant. Todos os dados são filtrados por user_id.
    Fase 2: adicionar Organization e migrar user_id → org_id nas entidades.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="Email único — usado para login",
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Hash bcrypt da senha",
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Nome completo do usuário",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Usuário ativo? Desativar em vez de deletar",
    )

    # Relacionamentos
    catalogs: Mapped[list[Catalog]] = relationship(  # type: ignore[name-defined]
        "Catalog",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"
