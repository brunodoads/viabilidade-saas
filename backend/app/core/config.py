from typing import Any
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Aplicação ----
    APP_NAME: str = "Viabilidade SaaS"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ---- Banco de Dados ----
    DATABASE_URL: str

    # ---- Redis / Celery ----
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # ---- JWT / Segurança ----
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 horas

    # ---- Upload ----
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # ---- Mercado Livre ----
    MERCADOLIVRE_BASE_URL: str = "https://api.mercadolibre.com"
    MERCADOLIVRE_SITE_ID: str = "MLB"

    # Credenciais OAuth — obter em https://developers.mercadolivre.com.br
    # A busca pública (/sites/MLB/search?q=...) exige autenticação server-side.
    # Use o fluxo "App Token" (sem usuário, apenas credenciais do app).
    ML_APP_ID: str = ""
    ML_CLIENT_SECRET: str = ""

    # Throttle entre requisições — respeitar rate limit (~10 req/s sem app, mais com app)
    ML_REQUEST_DELAY_SECONDS: float = 0.5

    # Máximo de retries por requisição antes de desistir
    ML_MAX_RETRIES: int = 3

    # Confiança mínima do matching para incluir anúncio na análise
    ML_MIN_MATCH_CONFIDENCE: float = 0.60

    ML_FEE_PCT: float = 15.0
    MIN_SALES_THRESHOLD: int = 1000

    # ---- Claude API ----
    CLAUDE_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-3-5-haiku-20241022"

    # ---- CORS ----
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3001"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> Any:
        # Aceita string única ou lista separada por vírgula
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(',') if o.strip()]


settings = Settings()
