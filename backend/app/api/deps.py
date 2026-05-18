"""
FastAPI Dependencies — injetados via Depends() nas rotas.
"""

import uuid

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.exceptions import CredentialsException
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repo import UserRepository

# Esquema OAuth2 — aponta para o endpoint de login
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency que extrai e valida o usuário do JWT.

    Lança CredentialsException (401) se:
    - Token ausente ou inválido
    - Token expirado
    - Usuário não encontrado ou inativo
    """
    payload = decode_access_token(token)
    if payload is None:
        raise CredentialsException()

    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        raise CredentialsException()

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise CredentialsException()

    repo = UserRepository(db)
    user = repo.get_by_id(user_id)

    if user is None or not user.is_active:
        raise CredentialsException()

    return user
