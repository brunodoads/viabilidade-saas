"""
Claude API Client — Normalização de Nomes de Produtos.

Uso MVP:
- Recebe lista de nomes brutos de produtos
- Retorna lista de nomes normalizados, limpos e padronizados
- Enviado em batch para economizar chamadas de API

Exemplos de normalização:
    "KIT LED 12V 5W BIVO" → "Kit Luminária LED 12V 5W Bivolt"
    "FRASCO PLASTICO 500ML C/TAMPA" → "Frasco Plástico 500ml com Tampa"
    "CX PAPELAO 50X30X20" → "Caixa de Papelão 50x30x20cm"
"""

import json
import logging

import anthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

# Prompt do sistema para normalização
SYSTEM_PROMPT = """Você é um especialista em nomenclatura de produtos para e-commerce brasileiro.
Sua tarefa é normalizar nomes de produtos de catálogos de importadoras/distribuidoras.

Regras de normalização:
1. Capitalize corretamente (não ALL CAPS, não all lower)
2. Escreva por extenso abreviações comuns (ex: "CX" → "Caixa", "KIT" permanece, "BIVO" → "Bivolt")
3. Use português correto com acentos
4. Mantenha especificações técnicas importantes (voltagem, capacidade, tamanho)
5. Remova códigos internos do fornecedor que não ajudam na busca
6. Seja conciso mas descritivo — como apareceria num anúncio do Mercado Livre
7. Máximo 80 caracteres

Responda APENAS com um JSON array de strings, na mesma ordem dos produtos recebidos.
Sem explicações, sem markdown, apenas o JSON."""


def normalize_product_names(raw_names: list[str]) -> list[str | None]:
    """
    Normaliza nomes de produtos via Claude API em batch.

    Args:
        raw_names: Lista de nomes brutos extraídos do catálogo

    Returns:
        Lista de nomes normalizados (mesma ordem dos inputs)
        None para produtos que não puderam ser normalizados
    """
    if not raw_names:
        return []

    if not settings.CLAUDE_API_KEY:
        logger.warning("CLAUDE_API_KEY não configurada — usando nomes originais")
        return [None] * len(raw_names)

    # Processar em batches de 50 para evitar tokens excessivos
    batch_size = 50
    results: list[str | None] = []

    for i in range(0, len(raw_names), batch_size):
        batch = raw_names[i:i + batch_size]
        batch_results = _normalize_batch(batch)
        results.extend(batch_results)

    return results


def _normalize_batch(names: list[str]) -> list[str | None]:
    """Normaliza um batch de até 50 nomes em uma única chamada de API."""
    client = anthropic.Anthropic(api_key=settings.CLAUDE_API_KEY)

    # Montar prompt com a lista numerada
    user_message = "Normalize os seguintes nomes de produtos:\n\n"
    user_message += json.dumps(names, ensure_ascii=False)

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message}
            ],
        )

        raw_response = response.content[0].text.strip()

        # Limpar possível markdown (```json ... ```)
        if raw_response.startswith("```"):
            raw_response = raw_response.split("```")[1]
            if raw_response.startswith("json"):
                raw_response = raw_response[4:]
        raw_response = raw_response.strip()

        normalized = json.loads(raw_response)

        if not isinstance(normalized, list):
            raise ValueError("Resposta não é uma lista JSON")

        # Garantir mesmo tamanho que o input
        result = []
        for i, name in enumerate(names):
            if i < len(normalized) and isinstance(normalized[i], str) and normalized[i].strip():
                result.append(normalized[i].strip())
            else:
                result.append(None)  # Fallback para nome original

        return result

    except json.JSONDecodeError as exc:
        logger.error("Claude: resposta inválida (JSON decode error): %s", exc)
        return [None] * len(names)
    except anthropic.APIError as exc:
        logger.error("Claude API error: %s", exc)
        return [None] * len(names)
    except Exception as exc:
        logger.error("Claude: erro inesperado na normalização: %s", exc, exc_info=True)
        return [None] * len(names)
