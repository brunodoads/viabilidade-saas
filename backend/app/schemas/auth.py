import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserRegisterRequest(BaseModel):
    """Payload para criar nova conta."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=100)
    full_name: str = Field(min_length=2, max_length=255)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v):
            raise ValueError("Senha deve conter ao menos um número")
        return v


class UserLoginRequest(BaseModel):
    """Payload para autenticar usuário."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Resposta de autenticação bem-sucedida."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Validade em segundos")


class UserResponse(BaseModel):
    """Dados públicos do usuário autenticado."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    created_at: datetime
