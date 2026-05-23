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

    ML_APP_ID: str = ""
    ML_CLIENT_SECRET: str = ""
    ML_REQUEST_DELAY_SECONDS: float = 0.5
    ML_MAX_RETRIES: int = 3
    # Confiança mínima de matching: 0.40 = aceitável para MVP sem credenciais ML
    # Com credenciais reais e sold_quantity disponível, aumentar para 0.60+
    # Confiança mínima de matching: 0.15 = diagnóstico (quase tudo passa).
    # Aumentar para 0.40+ após confirmar que pipeline produz dados.
    ML_MIN_MATCH_CONFIDENCE: float = 0.15

    # Taxa de venda do ML — depende do tipo de anúncio e categoria:
    #   Clássico: 11% (mais comum para iniciantes)
    #   Premium:  16% (mais visibilidade, custo maior)
    # Simulador oficial: https://www.mercadolivre.com.br/simulador-de-custos
    ML_FEE_PCT: float = 11.0

    # Custo médio de frete via Mercado Envios (obrigatório p/ frete grátis).
    # O ML cobre 50% para vendedores com boa reputação (MercadoLíder).
    # Para novos vendedores sem reputação, o custo total é ~R$45.
    # Padrão R$20 = estimativa conservadora para vendedor com reputação média.
    ML_SHIPPING_COST_BRL: float = 20.0

    # Imposto sobre vendas (% do valor de venda).
    # Simples Nacional Anexo I (comércio): varia de 4% (< R$180k/ano) a 19,5%.
    # Padrão 7% = estimativa para vendedor com faturamento entre R$180k-360k.
    ML_TAX_PCT: float = 7.0

    # Margem líquida mínima para considerar um produto VIÁVEL (%).
    # Abaixo desse threshold, o produto é classificado como EVITAR.
    # Padrão 20% = margem mínima recomendada para e-commerce sustentável.
    ML_MIN_VIABLE_MARGIN_PCT: float = 20.0

    # Threshold de vendas mínimas: 0 = sem filtro (modo diagnóstico).
    # Sem auth ML, sold_quantity=0 em todos os anúncios — filtro >0 eliminaria tudo.
    # Aumentar para 100+ apenas após confirmar autenticação ML funcionando.
    MIN_SALES_THRESHOLD: int = 0

    # ---- Apify (busca ML) ----
    # APIFY_API_TOKEN: obter em console.apify.com/settings/integrations
    # Necessário porque /sites/MLB/search está bloqueado para developers sem certificação.
    # Custo: ~$1/1000 resultados. MVP com 400 produtos ≈ $0.03.
    APIFY_API_TOKEN: str = ""
    APIFY_ML_ACTOR_ID: str = "karamelo~mercadolivre-scraper-brasil-portugues"

    # ---- Claude API ----
    CLAUDE_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-3-5-haiku-20241022"

    # ---- CORS ----
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3001"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> Any:
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(',') if o.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


settings = Settings()
