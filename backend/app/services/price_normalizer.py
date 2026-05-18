"""
Price Normalizer — Parsing robusto de preços para o mercado brasileiro.

Problema central:
    Catálogos brasileiros usam formatos inconsistentes e às vezes ambíguos.
    openpyxl pode retornar float, int ou string dependendo da célula.

Formatos suportados:
    ┌─────────────────────┬──────────────────┬──────────┐
    │ Input               │ Interpretação    │ Resultado│
    ├─────────────────────┼──────────────────┼──────────┤
    │ 1234.56 (float)     │ Numérico direto  │ 1234.56  │
    │ "R$ 1.234,56"       │ pt-BR com símbolo│ 1234.56  │
    │ "1.234,56"          │ pt-BR            │ 1234.56  │
    │ "1234,56"           │ pt-BR sem milhar │ 1234.56  │
    │ "1,234.56"          │ en-US            │ 1234.56  │
    │ "1234.56"           │ en-US simples    │ 1234.56  │
    │ "1.234"             │ pt-BR milhar     │ 1234.00  │
    │ "1,234"             │ en milhar        │ 1234.00  │
    │ "1234"              │ inteiro          │ 1234.00  │
    │ "50,00"             │ pt-BR 2 decimais │ 50.00    │
    │ "R$50"              │ sem decimais     │ 50.00    │
    │ "abc", None, ""     │ inválido         │ None     │
    └─────────────────────┴──────────────────┴──────────┘

Regra heurística para ambiguidade:
    "1.234" → se exatamente 3 dígitos após o ponto → milhar → 1234.00
    "1,234" → se exatamente 3 dígitos após a vírgula → milhar → 1234.00
    "1.5"   → apenas 1 dígito após o ponto → decimal → 1.50
    "1,5"   → apenas 1 dígito após a vírgula → decimal → 1.50
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

# Símbolos de moeda a remover
_CURRENCY_PATTERN = re.compile(r"[R$€£¥₩\$]", re.IGNORECASE)
# Caracteres não numéricos exceto . e ,
_NON_NUMERIC = re.compile(r"[^\d.,\-]")


def normalize_price(value: Any) -> Decimal | None:
    """
    Converte qualquer representação de preço para Decimal.

    Args:
        value: Valor da célula — pode ser float, int, str ou None

    Returns:
        Decimal positivo ou None se o valor for inválido/não parseável
    """
    if value is None:
        return None

    # Valores numéricos diretos (openpyxl retorna float para células de número)
    if isinstance(value, (int, float)):
        return _from_numeric(value)

    # String — precisa de parsing
    if isinstance(value, str):
        return _from_string(value)

    # Outros tipos (bool, etc.) — ignorar
    return None


def _from_numeric(value: int | float) -> Decimal | None:
    """Converte numérico Python direto para Decimal."""
    try:
        d = Decimal(str(value))
        return d if d > 0 else None
    except InvalidOperation:
        return None


def _from_string(raw: str) -> Decimal | None:
    """
    Parseia string de preço com detecção automática de formato.

    Etapas:
    1. Limpar símbolos de moeda e espaços
    2. Detectar estilo de separador (pt-BR vs en-US vs ambíguo)
    3. Converter para Decimal
    """
    if not raw or not raw.strip():
        return None

    # Normalizar espaços e unicode (ex: espaço não-quebrável)
    cleaned = unicodedata.normalize("NFKC", raw).strip()

    # Remover símbolos de moeda
    cleaned = _CURRENCY_PATTERN.sub("", cleaned)

    # Remover espaços e caracteres inválidos (exceto . e ,)
    cleaned = _NON_NUMERIC.sub("", cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        return None

    try:
        result = _parse_number_string(cleaned)
        if result is not None and result > 0:
            return result
        return None
    except Exception:
        return None


def _parse_number_string(s: str) -> Decimal | None:
    """
    Converte string numérica com . e , para Decimal.

    Algoritmo de detecção de formato:
    - Se ambos . e , presentes: o ÚLTIMO é o separador decimal
    - Se apenas , presente:
        - Se exatamente 3 dígitos após a única ,: é milhar → remover
        - Caso contrário: é decimal → substituir por .
    - Se apenas . presente:
        - Se exatamente 3 dígitos após o único .: é milhar → remover
        - Caso contrário: é decimal → manter
    - Se nenhum: é inteiro
    """
    if not s:
        return None

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        # Ex: "1.234,56" ou "1,234.56"
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")

        if last_comma > last_dot:
            # pt-BR: "1.234,56" — vírgula é decimal
            clean = s.replace(".", "").replace(",", ".")
        else:
            # en-US: "1,234.56" — ponto é decimal
            clean = s.replace(",", "")

        return Decimal(clean)

    elif has_comma and not has_dot:
        # Só vírgula
        parts = s.split(",")
        if len(parts) == 2:
            after_comma = parts[1]
            if len(after_comma) == 3 and parts[0].isdigit():
                # "1,234" — vírgula é separador de milhar (en-US)
                return Decimal(s.replace(",", ""))
            else:
                # "123,45" — vírgula é decimal (pt-BR)
                return Decimal(s.replace(",", "."))
        else:
            # Múltiplas vírgulas — formato incomum, tentar remover todas exceto última
            last_comma = s.rfind(",")
            integer_part = s[:last_comma].replace(",", "")
            decimal_part = s[last_comma + 1:]
            return Decimal(f"{integer_part}.{decimal_part}")

    elif has_dot and not has_comma:
        # Só ponto
        parts = s.split(".")
        if len(parts) == 2:
            after_dot = parts[1]
            if len(after_dot) == 3 and parts[0].isdigit() and len(parts[0]) >= 1:
                # "1.234" — ponto é separador de milhar (pt-BR)
                # Mas "1.500" pode ser 1.5 em contexto financeiro...
                # Heurística adicional: se parte inteira > 9, provavelmente milhar
                # Para catálogos: custo raramente tem 3 decimais exatos
                if int(parts[0]) > 0:
                    return Decimal(s.replace(".", ""))
                return Decimal(s)
            else:
                # "123.45" ou "1.5" — ponto é decimal
                return Decimal(s)
        elif len(parts) > 2:
            # "1.234.567" — múltiplos pontos, todos separadores de milhar
            return Decimal(s.replace(".", ""))
        else:
            return Decimal(s)

    else:
        # Sem separadores — número inteiro
        return Decimal(s)


# ── Funções utilitárias públicas ──────────────────────────────────────────────

def is_valid_cost(value: Decimal | None, max_reasonable: Decimal = Decimal("999999")) -> bool:
    """
    Verifica se um custo é razoável para um produto de catálogo.

    Rejeita:
    - None
    - Valores negativos
    - Zero
    - Valores absurdamente altos (provavelmente erro de parsing)
    """
    if value is None:
        return False
    return Decimal("0.01") <= value <= max_reasonable


def format_price_br(value: Decimal) -> str:
    """Formata Decimal para exibição no padrão brasileiro. Ex: 1234.56 → 'R$ 1.234,56'"""
    int_part = int(value)
    decimal_part = int(round((value - int_part) * 100))
    formatted_int = f"{int_part:,}".replace(",", ".")
    return f"R$ {formatted_int},{decimal_part:02d}"
