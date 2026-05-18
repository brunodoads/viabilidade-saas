"""
ML Matching - Algoritmo de correspondencia entre catalogo e Mercado Livre.

OBJETIVO:
    Dado um produto do catalogo (ex: "Luva de Vinil Descartavel Sem Po P")
    e um anuncio do ML (ex: "Kit 100 Luvas Vinil S/ Po P"), determinar se
    sao o mesmo produto e com qual nivel de confianca.

ESTRATEGIA (sem embeddings, sem IA -- so heuristica robusta):
    1. Normalizacao: remove acentos, lowercase, tokeniza
    2. Tokens ponderados: material > cor > tamanho > generico
    3. Similaridade Jaccard sobre tokens-chave
    4. Penalizadores para falsos positivos:
       - Kit/combo/pacote: preco artificialmente alto
       - Voltagem diferente: produto errado
       - Tamanho diferente: variante incorreta
       - Material diferente: categoria diferente
       - Unidade diferente (kg vs L): produto diferente
    5. Score final = similaridade_base * (1 - soma_penalidades) + boost

TIERS DE CONFIANCA:
    >= 0.80 -> HIGH   -- usar para precificacao sem ressalvas
    0.60-0.79 -> MEDIUM -- usar com flag "conferir"
    < 0.60 -> LOW    -- descartar

LIMITACOES CONHECIDAS (aceitaveis para MVP):
    - Marcas sem dicionario: "Luva Nimax" vs "Luva Volk" nao detecta diferenca
    - Kits muito parecidos com item individual (ex: "Kit 1 Luva")
    - Abreviacoes raras: "S/P" vs "SP" vs "sem po"

Fase 2 (fora do escopo MVP):
    - Embeddings semanticos (sentence-transformers)
    - Dicionario de marcas por categoria
    - Analise de imagem para confirmacao visual
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Stop words ─────────────────────────────────────────────────────────────────

STOP_WORDS = frozenset([
    "de", "da", "do", "dos", "das", "com", "para", "por", "em", "e", "ou",
    "a", "o", "um", "uma", "ao", "na", "no", "nas", "nos", "se", "que",
    "ate", "mais", "menos", "muito", "pouco", "qual", "quais",
    "for", "with", "the", "and", "or", "of", "in", "to",
    "original", "novo", "nova", "lancamento", "promocao",
    "gratis", "frete", "envio", "rapido",
    # Palavras de estrutura em titulos ML (ruido semantico)
    "tamanho", "sem", "cor", "tipo", "modelo", "produto",
])

# ── Tokens de alto peso semantico ─────────────────────────────────────────────

HIGH_WEIGHT_TOKENS = frozenset([
    # Materiais
    "vinil", "nitrilo", "latex", "latice", "neoprene", "poliuretano",
    "plastico", "borracha", "silicone", "polietileno", "polipropileno",
    "aluminio", "aco", "inox", "ferro", "cobre", "madeira",
    "tnt", "spunbond",
    # Categorias de produto
    "luva", "mascara", "avental", "touca", "oculos", "bota", "sapato",
    "caneta", "esferografica", "lapis", "papel", "caixa", "saco", "sacola",
    "frasco", "garrafa", "pote", "bandeja", "embalagem",
    "secador", "liquidificador", "batedeira", "mixer", "ventilador",
    # Qualificadores importantes
    "descartavel", "descartaveis", "reutilizavel", "esteril", "esterilizado",
    "industrial", "cirurgico", "hospitalar",
    "ondulada", "microondulada", "duplo",
])

# ── Padroes de Kit/Combo ───────────────────────────────────────────────────────

KIT_PATTERNS = [
    r"\bkit\b",
    r"\bcombo\b",
    r"\bpack\b",
    r"\bpacote\b",
    r"\bconjunto\b",
    r"\bcaixa\s+com\s+\d+",
    r"\bcom\s+\d+\s+(?:unid|pares|pecas|pcs)",
    r"\b\d+\s+(?:unidades|unid|pares|pecas|pcs)\b",
    r"\bpar\s+de\b",
    r"\b\d+x\d+",
]

# ── Padroes de Voltagem ───────────────────────────────────────────────────────

VOLTAGE_PATTERNS = {
    "110": r"\b(?:110|127)\s*v\b",
    "220": r"\b220\s*v\b",
    "bivolt": r"\bbivolt\b",
}

# ── Padroes de Tamanho ────────────────────────────────────────────────────────

SIZE_TOKENS_CLOTHING = frozenset([
    "pp", "p", "m", "g", "gg", "xg", "xxg", "xs", "s", "l", "xl", "xxl"
])

SIZE_PATTERN_VOLUME = re.compile(
    r"\b(\d+(?:[,\.]\d+)?)\s*(ml|l|litros?|litro|kg|g|gramas?|grama)\b",
    re.IGNORECASE,
)

# ── Padroes de Unidade de Medida ──────────────────────────────────────────────

UNIT_GROUPS = [
    frozenset(["ml", "l", "litro", "litros"]),
    frozenset(["g", "kg", "grama", "gramas", "quilo"]),
    frozenset(["m", "cm", "mm"]),
]

# ── Separar digito+unidade colados: "500ml" -> "500 ml" ───────────────────────
# Excecao: "a4" nao e separado (A4 = formato de papel, nao unidade de medida)

_UNIT_SPLIT_RE = re.compile(
    r"(\d)(ml|kg|g|w|v|cm|mm|m|l)(?=\s|$)",
    re.IGNORECASE,
)

# ── Canonicalizar abreviacao "l" de litro apos numero: "1 l" -> "1 litro" ────
# Evita conflito com tamanho de roupa "L": "Luva L" nao tem numero antes.
# Aplica APOS o unit split, enquanto texto ainda tem "l" minusculo isolado.

_LITER_ABBREV_RE = re.compile(r"(\b\d+)\s+l\b", re.IGNORECASE)

# ── Normalizacao de sinonimos ─────────────────────────────────────────────────
# Aplicada apos limpeza de caracteres especiais, antes de tokenizar.
# Colapsa variantes comuns em um token canonico unico.

_SYNONYM_PATTERNS: list[tuple] = [
    # Conectores USB — variantes colapsadas em token unico
    (re.compile(r"\busb\s+tipo\s+c\b"), "usbc"),
    (re.compile(r"\busb\s*c\b"), "usbc"),
    (re.compile(r"\busb\s+tipo\s+a\b"), "usba"),
    (re.compile(r"\busb\s*a\b"), "usba"),
    (re.compile(r"\busb\s+tipo\s+b\b"), "usbb"),
    (re.compile(r"\busb\s*b\b"), "usbb"),
    # Unidades de distancia — normaliza extenso para abreviacao
    (re.compile(r"\bmetros?\b"), "m"),
    (re.compile(r"\bcentimetros?\b"), "cm"),
    (re.compile(r"\bmilimetros?\b"), "mm"),
    (re.compile(r"\bpolicadas?\b"), "pol"),
    # Capacidade — normaliza extenso
    (re.compile(r"\blitros?\b"), "l"),
    (re.compile(r"\bkilogramas?\b"), "kg"),
    (re.compile(r"\bquilogramas?\b"), "kg"),
    (re.compile(r"\bgramas?\b"), "g"),
    # Abreviacoes comuns em titulos ML
    (re.compile(r"\bfls\b"), "folhas"),
    (re.compile(r"\bpcs\b"), "pecas"),
    (re.compile(r"\bpcs\b"), "pecas"),
    (re.compile(r"\bund\b"), "unid"),
    (re.compile(r"\bpct\b"), "pacote"),
    (re.compile(r"\bcx\b"), "caixa"),
    (re.compile(r"\bhd\b"), "hd"),
    (re.compile(r"\bwifi\b"), "wireless"),
    (re.compile(r"\bsem\s*fio\b"), "wireless"),
    (re.compile(r"\bbt\b"), "bluetooth"),
]


def _apply_synonyms(text: str) -> str:
    """Aplica normalizacao de sinonimos ao texto ja limpo (pos-clean)."""
    for pattern, replacement in _SYNONYM_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── Dataclasses de resultado ──────────────────────────────────────────────────

class ConfidenceTier:
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class MatchResult:
    """
    Resultado do matching entre produto do catalogo e anuncio do ML.

    score: float 0.0-1.0 (1.0 = match perfeito)
    tier: HIGH | MEDIUM | LOW
    reasons: lista de razoes para penalidades (debug)
    """
    score: float
    tier: str
    catalog_name: str
    ml_title: str
    reasons: list = field(default_factory=list)

    @classmethod
    def create(cls, score: float, catalog_name: str, ml_title: str, reasons: list) -> "MatchResult":
        if score >= 0.80:
            tier = ConfidenceTier.HIGH
        elif score >= 0.50:
            tier = ConfidenceTier.MEDIUM
        else:
            tier = ConfidenceTier.LOW
        return cls(
            score=round(score, 4),
            tier=tier,
            catalog_name=catalog_name,
            ml_title=ml_title,
            reasons=reasons,
        )

    @property
    def is_usable(self) -> bool:
        return self.score >= 0.50

    def __repr__(self) -> str:
        return f"<Match score={self.score:.0%} tier={self.tier} ml='{self.ml_title[:40]}'>"


# ── Funcao principal ───────────────────────────────────────────────────────────

def calculate_match_confidence(catalog_name: str, ml_title: str) -> MatchResult:
    """
    Calcula o score de confianca do matching entre catalogo e anuncio ML.

    Retorna MatchResult com score 0.0-1.0 e tier HIGH/MEDIUM/LOW.
    """
    reasons: list = []

    cat_norm = _normalize(catalog_name)
    ml_norm = _normalize(ml_title)

    cat_tokens = _tokenize(cat_norm)
    ml_tokens = _tokenize(ml_norm)

    if not cat_tokens or not ml_tokens:
        return MatchResult.create(0.0, catalog_name, ml_title, ["tokens vazios apos normalizacao"])

    base_score = _weighted_jaccard(cat_tokens, ml_tokens)

    total_penalty = 0.0
    total_boost = 0.0

    # 1. Kit/Combo no ML mas nao no catalogo
    kit_penalty = _check_kit_mismatch(cat_norm, ml_norm)
    if kit_penalty > 0:
        total_penalty += kit_penalty
        reasons.append(f"kit/combo detectado no ML ({kit_penalty:.0%} penalidade)")

    # 2. Voltagem conflitante OU boost para bivolt compativel
    voltage_penalty, bivolt_boost = _check_voltage(cat_norm, ml_norm)
    if voltage_penalty > 0:
        total_penalty += voltage_penalty
        reasons.append(f"voltagem conflitante ({voltage_penalty:.0%} penalidade)")
    elif bivolt_boost > 0:
        total_boost += bivolt_boost

    # 3. Tamanho/Roupa conflitante (completamente disjuntos)
    size_penalty = _check_clothing_size_mismatch(cat_tokens, ml_tokens)
    if size_penalty > 0:
        total_penalty += size_penalty
        reasons.append(f"tamanho diferente ({size_penalty:.0%})")

    # 4. Unidade de medida conflitante (kg vs L)
    unit_penalty = _check_unit_mismatch(cat_norm, ml_norm)
    if unit_penalty > 0:
        total_penalty += unit_penalty
        reasons.append(f"unidade diferente ({unit_penalty:.0%})")

    # 5. Volume/Peso conflitante (500ml vs 1L)
    volume_penalty = _check_volume_mismatch(cat_norm, ml_norm)
    if volume_penalty > 0:
        total_penalty += volume_penalty
        reasons.append(f"volume/peso diferente ({volume_penalty:.0%})")

    penalized_score = base_score * (1.0 - min(total_penalty, 1.0))
    final_score = max(0.0, min(1.0, penalized_score + total_boost))

    result = MatchResult.create(final_score, catalog_name, ml_title, reasons)

    if reasons:
        logger.debug(
            "Matching: '%s' x '%s' -> %.0f%% | %s",
            catalog_name[:30], ml_title[:30], final_score * 100, "; ".join(reasons),
        )

    return result


def filter_qualified_listings(
    catalog_name: str,
    listings,
    min_sales: int = 1000,
    min_confidence: float = 0.60,
) -> tuple:
    """
    Filtra anuncios do ML aplicando:
        1. Filtro de vendas minimas (sold_quantity >= min_sales)
        2. Filtro de matching (score >= min_confidence)

    Retorna (listings_aprovados, match_results_aprovados).
    """
    approved_listings = []
    approved_matches = []
    discarded_low_sales = 0
    discarded_low_match = 0

    for listing in listings:
        if listing.sold_quantity < min_sales:
            discarded_low_sales += 1
            continue

        match = calculate_match_confidence(catalog_name, listing.title)

        if not match.is_usable:
            discarded_low_match += 1
            logger.debug(
                "Matching DESCARTADO: score=%.0f%% | '%s' | motivos: %s",
                match.score * 100,
                listing.title[:50],
                "; ".join(match.reasons) if match.reasons else "baixa similaridade",
            )
            continue

        approved_listings.append(listing)
        approved_matches.append(match)

    logger.info(
        "Matching '%s' -> %d aprovados | %d sem vendas | %d sem match",
        catalog_name[:40],
        len(approved_listings),
        discarded_low_sales,
        discarded_low_match,
    )

    return approved_listings, approved_matches


def build_search_query(product_name: str) -> str:
    """
    Constroi a query de busca otimizada para o ML a partir do nome do produto.

    Remove tokens de quantidade, limita a 6 tokens relevantes.
    """
    norm = _normalize(product_name)
    tokens = _tokenize(norm)

    quantity_suffixes = {"unid", "unidades", "pcs", "pecas", "pares", "cx"}
    cleaned_tokens = []
    skip_next = False

    for i, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token.isdigit() and i + 1 < len(tokens) and tokens[i + 1] in quantity_suffixes:
            skip_next = True
            continue
        cleaned_tokens.append(token)

    query_tokens = cleaned_tokens[:6]
    query = " ".join(query_tokens)

    if not query:
        words = product_name.split()[:3]
        query = " ".join(words)

    return query


# ── Algoritmo de similaridade ─────────────────────────────────────────────────

def _weighted_jaccard(tokens_a: list, tokens_b: list) -> float:
    """Jaccard similarity com pesos por importancia semantica do token."""
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    intersection = set_a & set_b
    union = set_a | set_b

    if not union:
        return 0.0

    def weight(token: str) -> float:
        return 2.0 if token in HIGH_WEIGHT_TOKENS else 1.0

    w_intersection = sum(weight(t) for t in intersection)
    w_union = sum(weight(t) for t in union)

    if w_union == 0:
        return 0.0

    return w_intersection / w_union


# ── Deteccao de penalizadores ─────────────────────────────────────────────────

def _check_kit_mismatch(cat_norm: str, ml_norm: str) -> float:
    """Penaliza quando o ML e um kit mas o catalogo e item individual."""
    ml_is_kit = any(re.search(p, ml_norm) for p in KIT_PATTERNS)
    cat_is_kit = any(re.search(p, cat_norm) for p in KIT_PATTERNS)
    if ml_is_kit and not cat_is_kit:
        return 0.50
    return 0.0


def _extract_voltage(text: str) -> str | None:
    if re.search(VOLTAGE_PATTERNS["bivolt"], text):
        return "bivolt"
    if re.search(VOLTAGE_PATTERNS["110"], text):
        return "110"
    if re.search(VOLTAGE_PATTERNS["220"], text):
        return "220"
    return None


def _check_voltage(cat_norm: str, ml_norm: str) -> tuple:
    """
    Analisa voltagem e retorna (penalidade, boost).

    110v x 220v  -> penalidade 0.90
    110v x bivolt -> boost 0.15 (bivolt cobre ambas)
    bivolt x 220v -> boost 0.15
    sem voltagem  -> (0.0, 0.0)
    """
    cat_v = _extract_voltage(cat_norm)
    ml_v = _extract_voltage(ml_norm)

    if not cat_v or not ml_v:
        return 0.0, 0.0

    if cat_v == ml_v:
        return 0.0, 0.0

    if cat_v == "bivolt" or ml_v == "bivolt":
        return 0.0, 0.15

    return 0.90, 0.0


def _check_voltage_mismatch(cat_norm: str, ml_norm: str) -> float:
    """Alias para retrocompatibilidade."""
    penalty, _ = _check_voltage(cat_norm, ml_norm)
    return penalty


def _check_clothing_size_mismatch(cat_tokens: list, ml_tokens: list) -> float:
    """
    Penaliza quando tamanhos de roupa/EPI sao COMPLETAMENTE disjuntos.

    Usa disjunto (nao igualdade) para evitar falso positivo quando:
    - ML lista tamanho do catalogo + outros tamanhos
    - "S" no ML e abreviacao de "Sem" (S/Po) e nao tamanho Small
    """
    cat_sizes = {t for t in cat_tokens if t in SIZE_TOKENS_CLOTHING}
    ml_sizes = {t for t in ml_tokens if t in SIZE_TOKENS_CLOTHING}

    # So penaliza se AMBOS tem tamanho E nao ha interseccao
    if cat_sizes and ml_sizes and not (cat_sizes & ml_sizes):
        return 0.70

    return 0.0


def _check_unit_mismatch(cat_norm: str, ml_norm: str) -> float:
    """Penaliza quando unidades de medida sao de grupos incompativeis (kg vs L)."""
    def find_unit_group(text: str) -> int | None:
        for i, group in enumerate(UNIT_GROUPS):
            for unit in group:
                if re.search(r"\b" + unit + r"\b", text):
                    return i
        return None

    cat_group = find_unit_group(cat_norm)
    ml_group = find_unit_group(ml_norm)

    if cat_group is not None and ml_group is not None and cat_group != ml_group:
        return 0.80

    return 0.0


def _check_volume_mismatch(cat_norm: str, ml_norm: str) -> float:
    """
    Penaliza quando volumes/pesos sao significativamente diferentes.

    Converte para unidade base (ml ou g) e compara.
    Tolerancia: +/- 25%.

    Exemplos:
        500ml vs 1L -> 500 vs 1000 -> diferenca > 25% -> penalidade 0.60
        1L vs 1000ml -> iguais -> sem penalidade
        500g vs 0.5kg -> iguais -> sem penalidade
    """
    def extract_volumes(text: str) -> list:
        volumes = []
        for m in SIZE_PATTERN_VOLUME.finditer(text):
            value_str = m.group(1).replace(",", ".")
            unit = m.group(2).lower()
            try:
                value = float(value_str)
                if unit in ("l", "litro", "litros"):
                    value *= 1000
                elif unit == "kg":
                    value *= 1000
                volumes.append(value)
            except ValueError:
                continue
        return volumes

    cat_vols = extract_volumes(cat_norm)
    ml_vols = extract_volumes(ml_norm)

    if not cat_vols or not ml_vols:
        return 0.0

    cat_vol = max(cat_vols)
    ml_vol = max(ml_vols)

    if cat_vol == 0 or ml_vol == 0:
        return 0.0

    ratio = cat_vol / ml_vol
    if ratio < 0.75 or ratio > 1.33:
        return 0.60

    return 0.0


# ── Normalizacao e tokenizacao ────────────────────────────────────────────────



# ── Normalizacao e tokenizacao ────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Normaliza texto para comparacao:
        - Remove acentos (unicodedata NFKD)
        - Lowercase
        - Separa digito+unidade colados: "500ml" -> "500 ml", "1l" -> "1 l"
        - Canonicaliza abreviacao de litro: "1 l" -> "1 litro"
          (evita confusao com tamanho de roupa "L" que nao segue numero)
        - Remove caracteres nao-alfanumericos exceto espaco
        - Aplica normalizacao de sinonimos (USB-C, metros, etc.)
        - Normaliza espacos multiplos
    """
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    lowered = ascii_text.lower()
    split_units = _UNIT_SPLIT_RE.sub(r"\1 \2", lowered)
    canonical = _LITER_ABBREV_RE.sub(r"\1 litro", split_units)
    cleaned = re.sub(r"[^a-z0-9\s]", " ", canonical)
    synonymized = _apply_synonyms(cleaned)
    return " ".join(synonymized.split())


def _tokenize(text: str) -> list:
    """
    Tokeniza texto normalizado em lista de tokens relevantes.

    Remove stop words, tokens muito curtos (exceto tamanhos de roupa e digitos)
    e numeros grandes (quantidades > 999).
    """
    if not text:
        return []

    raw_tokens = text.split()
    tokens = []

    for token in raw_tokens:
        if token in STOP_WORDS:
            continue
        if len(token) == 1 and token not in SIZE_TOKENS_CLOTHING and not token.isdigit():
            continue
        if token.isdigit() and int(token) > 999:
            continue
        tokens.append(token)

    return tokens
