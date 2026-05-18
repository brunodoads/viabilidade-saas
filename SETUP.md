# Setup Local — Viabilidade de Produtos

> Sistema de inteligência comercial: upload de catálogo → análise ML → oportunidades rankeadas.

---

## Pré-requisitos

| Ferramenta | Versão mínima | Instalação |
|-----------|--------------|------------|
| Docker Desktop | 24+ | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose V2 | incluído no Docker Desktop | — |
| Python | 3.11+ | Apenas para scripts utilitários locais |

---

## Opção 1 — Setup Automático (recomendado)

```bash
# 1. Clonar / entrar no projeto
cd "IA de Viabilidade de Produtos"

# 2. Rodar setup completo
./start.sh
```

O script irá:
- Verificar pré-requisitos
- Criar `backend/.env` a partir de `.env.docker` (se não existir)
- Subir todos os containers com `docker compose up --build`
- Aguardar o backend ficar saudável
- Criar usuário de teste via API

Acesse: **http://localhost:3000** com `admin@viabilidade.com` / `admin123`

---

## Opção 2 — Setup Manual (desenvolvimento)

### 2.1 Infraestrutura (banco + redis)

```bash
# Subir apenas postgres e redis
docker compose up -d postgres redis

# Verificar que estão saudáveis
docker compose ps
```

### 2.2 Backend

```bash
cd backend

# Instalar dependências
pip install poetry
poetry install

# Configurar ambiente
cp .env.example .env
# Editar .env com suas chaves (SECRET_KEY obrigatório)

# Aplicar migrations
alembic upgrade head

# Validar ambiente
python ../scripts/validate_env.py

# Iniciar API
uvicorn app.main:app --reload --port 8000
```

### 2.3 Celery Worker (terminal separado)

```bash
cd backend
celery -A app.workers.celery_app worker --loglevel=info
```

### 2.4 Frontend

```bash
cd frontend
./setup.sh   # npm install + .env.local
npm run dev  # http://localhost:3000
```

---

## Configuração de Variáveis

### Obrigatórias

| Variável | Descrição | Como obter |
|---------|-----------|-----------|
| `SECRET_KEY` | Chave JWT | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | PostgreSQL | Já configurada no docker-compose |

### Para pesquisa no Mercado Livre

| Variável | Descrição | Como obter |
|---------|-----------|-----------|
| `ML_APP_ID` | App ID do ML | [developers.mercadolivre.com.br](https://developers.mercadolivre.com.br) |
| `ML_CLIENT_SECRET` | Client Secret do ML | Mesmo portal |

> **Sem credenciais ML:** o sistema funciona mas a etapa de pesquisa de mercado falha.
> É possível testar o upload e parsing sem elas.

### Opcionais

| Variável | Padrão | Descrição |
|---------|--------|-----------|
| `ML_FEE_PCT` | `15.0` | Taxa do Mercado Livre em % |
| `ML_MIN_MATCH_CONFIDENCE` | `0.60` | Confiança mínima do matching |
| `MIN_SALES_THRESHOLD` | `1000` | Vendas mínimas para qualificar anúncio |
| `CLAUDE_API_KEY` | — | Para agentes IA (não usado no MVP) |

---

## Checklist Ponta-a-Ponta

### ✅ Infraestrutura

- [ ] `docker compose ps` mostra `postgres` e `redis` como `healthy`
- [ ] Backend responde: `curl http://localhost:8000/health`
- [ ] API Docs acessível: http://localhost:8000/docs
- [ ] Frontend carrega: http://localhost:3000

### ✅ Autenticação

- [ ] Login com `admin@viabilidade.com` / `admin123` funciona
- [ ] Token JWT recebido e armazenado no localStorage
- [ ] Dashboard exibe lista de catálogos (vazia inicialmente)

### ✅ Upload de Catálogo

- [ ] Arrastar/soltar um `.xlsx` ou `.csv` funciona
- [ ] Upload retorna `catalog_id` e redireciona para `/catalogs/{id}`
- [ ] Status inicia como `PENDING` ou `PROCESSING`

### ✅ Pipeline de Processamento

- [ ] Status muda de `PENDING` → `PROCESSING` → `READY`
- [ ] Progress bar avança progressivamente
- [ ] Polling de 5s está ativo (verificar no DevTools → Network)
- [ ] Quando `READY`, tabela de oportunidades aparece

### ✅ Oportunidades

- [ ] Tabela mostra produtos rankeados por score
- [ ] Badges EXCELENTE/BOA/ARRISCADA/EVITAR aparecem com cores corretas
- [ ] Filtro por score mínimo funciona
- [ ] Filtro por classificação funciona
- [ ] Contadores por classificação aparecem no rodapé

---

## Migrations

```bash
cd backend

# Verificar estado atual
alembic current

# Aplicar todas as migrations
alembic upgrade head

# Ver histórico
alembic history

# Reverter uma versão (cuidado!)
alembic downgrade -1
```

**Ordem das migrations:**
```
001_initial_schema          — tabelas base
002_add_catalog_parse_metadata — parse_metadata JSONB
003_add_new_fields_rename_enum — novos campos + enum EXCELENTE/BOA/ARRISCADA
```

---

## Validação do Ambiente

```bash
# Valida conexões, imports e estado das migrations
python scripts/validate_env.py
```

Saída esperada (verde):
```
── PostgreSQL ─────────────────────────────────
  ✓  Conectado — PostgreSQL 16.x...
── Redis ──────────────────────────────────────
  ✓  Conectado — Redis 7.x em redis://localhost:6379/0
── Migrations Alembic ─────────────────────────
  ✓  Revision atual: 003
  ✓  Banco em dia com o código
══════════════════════════════════════════
  Passou: 14   Avisos: 2   Erros: 0
══════════════════════════════════════════
Sistema pode subir, mas verifique os avisos.
```

---

## Comandos Úteis

```bash
# Ver logs em tempo real
docker compose logs -f backend
docker compose logs -f worker

# Reiniciar apenas o backend
docker compose restart backend

# Parar tudo (preserva dados)
docker compose down

# Parar tudo E apagar volumes (CUIDADO: apaga o banco!)
docker compose down -v

# Rodar migrations manualmente dentro do container
docker compose exec backend alembic upgrade head

# Abrir shell no container do backend
docker compose exec backend bash

# Ver status de todos os containers
docker compose ps
```

---

## Troubleshooting

### Backend não sobe

```bash
docker compose logs backend
```
- **"could not connect to server"** → postgres ainda inicializando, aguarde
- **"FATAL: password authentication failed"** → verifique DATABASE_URL no .env
- **"alembic.util.exc.CommandError"** → erro na migration, veja os logs completos

### Celery não processa jobs

```bash
docker compose logs worker
```
- **"redis.exceptions.ConnectionError"** → Redis não está rodando
- **Tarefa fica em PENDING** → worker não está consumindo, reinicie: `docker compose restart worker`

### Frontend não conecta ao backend

- Verifique `NEXT_PUBLIC_API_URL` em `frontend/.env.local`
- Certifique-se que o backend está em `localhost:8000`
- CORS: o backend aceita `localhost:3000` por padrão

### Matching retorna 0 resultados

- Verifique `ML_APP_ID` e `ML_CLIENT_SECRET` no `.env`
- Teste a autenticação ML: `curl http://localhost:8000/docs` → `/auth/ml/token`
- Ajuste `ML_MIN_MATCH_CONFIDENCE` para um valor menor (ex: `0.40`)
