from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Engine principal — sync, compatível com Celery
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,       # Verifica conexão antes de usar do pool
    pool_size=10,             # Conexões permanentes no pool
    max_overflow=20,          # Conexões extras em pico
    pool_recycle=3600,        # Recicla conexão após 1h (evita timeout do PG)
    echo=settings.DEBUG,      # Loga SQL apenas em modo debug
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db() -> Generator[Session, None, None]:
    """
    Dependency FastAPI — abre e fecha sessão por request.

    Uso nas rotas:
        db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Verifica se o banco está acessível. Usado no startup da aplicação."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
