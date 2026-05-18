from fastapi import HTTPException, status


class CredentialsException(HTTPException):
    """401 — Token inválido, expirado ou ausente."""

    def __init__(self, detail: str = "Credenciais inválidas") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class NotFoundException(HTTPException):
    """404 — Recurso não encontrado."""

    def __init__(self, resource: str = "Recurso") -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource} não encontrado(a)",
        )


class ForbiddenException(HTTPException):
    """403 — Sem permissão para acessar este recurso."""

    def __init__(self, detail: str = "Acesso negado") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


class BadRequestException(HTTPException):
    """400 — Requisição inválida."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


class ConflictException(HTTPException):
    """409 — Conflito (ex: e-mail já cadastrado)."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )


class UnprocessableException(HTTPException):
    """422 — Entidade não processável (ex: arquivo inválido)."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )
