FROM python:3.11-slim

WORKDIR /app

# Dependencias do sistema necessarias para psycopg2, pymupdf e pdfplumber
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar pyproject.toml para extrair dependencias
COPY pyproject.toml ./

# Instalar todas as dependencias direto via pip (sem Poetry, sem lockfile)
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    "python-multipart>=0.0.18" \
    "sqlalchemy>=2.0.36" \
    "alembic>=1.14.0" \
    "psycopg2-binary>=2.9.10" \
    "pydantic>=2.10.0" \
    "pydantic-settings>=2.6.0" \
    "python-jose[cryptography]>=3.3.0" \
    "passlib[bcrypt]>=1.7.4" \
    "bcrypt==3.2.2" \
    "celery[redis]>=5.4.0" \
    "redis>=5.2.0" \
    "httpx>=0.28.0" \
    "anthropic>=0.40.0" \
    "openpyxl>=3.1.5" \
    "pandas>=2.2.0" \
    "pymupdf>=1.24.0" \
    "pdfplumber>=0.11.0" \
    "email-validator>=2.3.0"

# Copiar codigo
COPY . .

# Criar diretorio de uploads
RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
