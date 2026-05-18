from datetime import timedelta

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictException, CredentialsException
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.session import get_db
from app.repositories.user_repo import UserRepository
from app.schemas.auth import TokenResponse, UserRegisterRequest, UserResponse

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Criar nova conta",
)
def register(body: UserRegisterRequest, db: Session = Depends(get_db)) -> UserResponse:
    """
    Registra um novo usuário.

    Retorna os dados do usuário criado (sem senha).
    Lança 409 se o e-mail já estiver cadastrado.
    """
    repo = UserRepository(db)

    if repo.email_exists(body.email):
        raise ConflictException("E-mail já cadastrado")

    hashed = get_password_hash(body.password)
    user = repo.create_user(
        email=body.email,
        hashed_password=hashed,
        full_name=body.full_name,
    )
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Autenticar e receber token JWT",
)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    Autentica via e-mail + senha.

    Retorna JWT para uso nos endpoints protegidos.
    Compatível com OAuth2PasswordRequestForm (campo 'username' = e-mail).
    """
    repo = UserRepository(db)
    user = repo.get_by_email(form_data.username)  # username = email no form

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise CredentialsException("E-mail ou senha incorretos")

    if not user.is_active:
        raise CredentialsException("Conta desativada")

    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(subject=str(user.id), expires_delta=expires)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=int(expires.total_seconds()),
    )
