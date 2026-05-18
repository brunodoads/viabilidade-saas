#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Viabilidade de Produtos — Setup e Start
# ──────────────────────────────────────────────────────────────
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_ok()   { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC}  $1"; }
log_err()  { echo -e "${RED}✗${NC} $1"; }

echo ""
echo "═══════════════════════════════════════════"
echo "  Viabilidade de Produtos — Setup Local"
echo "═══════════════════════════════════════════"
echo ""

# ── Verificar pré-requisitos ──────────────────────────────────
echo "→ Verificando pré-requisitos..."

if ! command -v docker &>/dev/null; then
  log_err "Docker não encontrado. Instale: https://docs.docker.com/get-docker/"
  exit 1
fi
log_ok "Docker OK"

if ! command -v docker compose &>/dev/null; then
  log_err "Docker Compose V2 não encontrado."
  exit 1
fi
log_ok "Docker Compose OK"

# ── Verificar .env do backend ─────────────────────────────────
echo ""
echo "→ Verificando configuração..."

if [ ! -f ./backend/.env ]; then
  log_warn ".env não encontrado — copiando de .env.docker"
  cp ./backend/.env.docker ./backend/.env
  echo ""
  log_warn "ATENÇÃO: Configure backend/.env antes de continuar:"
  log_warn "  - SECRET_KEY  (obrigatório)"
  log_warn "  - ML_APP_ID e ML_CLIENT_SECRET (para busca no ML)"
  log_warn "  - CLAUDE_API_KEY (opcional no MVP)"
  echo ""
  read -p "Pressione Enter após configurar o .env (ou Ctrl+C para cancelar)..."
fi
log_ok ".env OK"

# ── Subir serviços ────────────────────────────────────────────
echo ""
echo "→ Subindo serviços (postgres, redis, backend, worker, frontend)..."
docker compose up -d --build

# ── Aguardar backend ──────────────────────────────────────────
echo ""
echo "→ Aguardando backend ficar pronto..."
MAX_WAIT=60
COUNT=0
until curl -sf http://localhost:8000/health > /dev/null 2>&1; do
  sleep 2
  COUNT=$((COUNT + 2))
  if [ $COUNT -ge $MAX_WAIT ]; then
    log_err "Backend não respondeu em ${MAX_WAIT}s"
    echo "Veja os logs: docker compose logs backend"
    exit 1
  fi
  echo -n "."
done
echo ""
log_ok "Backend rodando em http://localhost:8000"

# ── Criar usuário inicial ─────────────────────────────────────
echo ""
echo "→ Criando usuário de teste (se não existir)..."
python3 scripts/seed_user.py 2>/dev/null && log_ok "Usuário criado" || log_warn "Usuário já existe ou erro no seed"

echo ""
echo "═══════════════════════════════════════════"
log_ok "Sistema rodando!"
echo ""
echo "  Frontend:  http://localhost:3000"
echo "  Backend:   http://localhost:8000"
echo "  API Docs:  http://localhost:8000/docs"
echo ""
echo "  Login de teste:"
echo "    E-mail:  admin@viabilidade.com"
echo "    Senha:   admin123"
echo ""
echo "  Para parar: docker compose down"
echo "═══════════════════════════════════════════"
