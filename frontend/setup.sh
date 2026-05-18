#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Setup do frontend — Viabilidade de Produtos
# ──────────────────────────────────────────────────────────────
set -e

echo "→ Instalando dependências..."
npm install

echo "→ Copiando .env.local..."
if [ ! -f .env.local ]; then
  cp .env.local.example .env.local
  echo "  Criado .env.local — ajuste NEXT_PUBLIC_API_URL se necessário"
fi

echo ""
echo "✅ Setup concluído!"
echo ""
echo "Comandos:"
echo "  npm run dev    → inicia o servidor de desenvolvimento em :3000"
echo "  npm run build  → build de produção"
echo ""
echo "Certifique-se que o backend está rodando em localhost:8000"
