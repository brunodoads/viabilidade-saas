FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar Poetry
RUN pip install poetry==1.8.3

# Copiar arquivos de dependências
COPY pyproject.toml poetry.lock* ./

# Instalar dependências (sem dev)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Copiar código
COPY . .

# Criar diretório de uploads
RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
