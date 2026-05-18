"""
Column Detector — Mapeamento inteligente de colunas de catálogos XLSX.

Problema:
    Cada fornecedor nomeia suas colunas diferente. Precisamos mapear
    "Produto", "NOME DO ITEM", "Descrição", "Mercadoria" → mesmo campo semântico.

Algoritmo de scoring:
    1. Normalizar cabeçalho: lowercase + remove acentos + strip
    2. Para cada campo semântico (nome, custo, sku...):
       a. Match exato → score 1.0
       b. Cabeçalho contém keyword → score 0.85
       c. Keyword contém cabeçalho → score 0.75
       d. Cabeçalho começa com keyword → score 0.80
    3. Escolher campo com maior score (se > threshold)
    4. Nenhuma coluna disputa o mesmo campo (preferir o mais próximo)

Detecção de linha de cabeçalho:
    Escaneia as primeiras N linhas e puntua por:
    - Proporção de células string (não numéricas)
    - Número de correspondências com vocabulário conhecido
    - Comprimento médio das células (cabeçalhos são curtos)
"""

import unicodedata
from dataclasses import dataclass, field

from app.services.parse_result import ColumnMappingResult

# ── Vocabulário de sinônimos por campo semântico ──────────────────────────────
# Ordenados por especificidade (mais específico primeiro = menos falso positivo)

COLUMN_SYNONYMS: dict[str, list[str]] = {
    "product_name": [
        # Compostos específicos (verificar antes de parciais)
        "nome do produto", "nome produto", "descricao do produto", "descrição do produto",
        "nome do item", "descricao do item", "descrição do item",
        "denominacao do produto", "denominação do produto",
        "especificacao do produto", "especificação do produto",
        # Simples
        "produto", "descricao", "descrição", "denominacao", "denominação",
        "especificacao", "especificação", "mercadoria", "artigo", "material",
        "componente", "item", "nome", "titulo", "título",
        # Inglês
        "product", "description", "name", "item name", "product name",
        "product description", "article",
    ],
    "cost": [
        # Compostos específicos de custo
        "preco de custo", "preço de custo", "preco custo", "preço custo",
        "custo unitario", "custo unitário", "valor unitario", "valor unitário",
        "preco unitario", "preço unitário", "vl custo", "vlr custo",
        "vl unit", "vlr unit", "valor de custo",
        "preco de compra", "preço de compra",
        "custo de aquisicao", "custo de aquisição",
        # Catálogos de distribuidoras: "preço de venda" do fornecedor = custo do comprador
        "preco de venda", "preço de venda", "preco venda", "preço venda",
        "vl venda", "vlr venda", "valor venda",
        "preco tabela", "preço tabela", "tabela", "tab",
        "preco atacado", "preço atacado", "atacado",
        # Simples
        "custo", "preco", "preço", "valor", "vl", "vlr",
        "r$", "reais",
        # Inglês
        "cost", "price", "unit price", "unit cost", "buy price",
        "purchase price", "buying price",
    ],
    "sku": [
        # Compostos
        "codigo do produto", "código do produto", "cod produto",
        "codigo produto", "codigo do item", "número do produto",
        "numero do produto", "part number", "cod item", "ref produto",
        # Simples
        "sku", "codigo", "código", "cod", "ref", "referencia", "referência",
        "cód", "num", "numero", "número", "id",
        # Inglês
        "code", "product code", "item code", "partnumber", "pn",
    ],
    "category": [
        # Compostos
        "grupo de produto", "tipo de produto", "linha de produto",
        "familia de produto", "família de produto",
        # Simples
        "categoria", "grupo", "tipo", "familia", "família",
        "departamento", "linha", "segmento", "secao", "seção",
        "classificacao", "classificação", "subgrupo",
        # Inglês
        "category", "group", "type", "family", "department", "section",
    ],
    "supplier": [
        # Compostos
        "nome do fornecedor", "nome fornecedor",
        # Simples
        "fornecedor", "fabricante", "marca", "origem",
        "produtor", "vendedor",
        # Inglês
        "supplier", "brand", "manufacturer", "origin", "vendor",
    ],
}

# Colunas que devem ser IGNORADAS (frequentes mas sem valor semântico)
# ATENÇÃO: NÃO incluir aqui colunas de preço/valor, pois em catálogos de
# distribuidoras o "preço de venda" DO FORNECEDOR é o custo do comprador.
IGNORE_COLUMNS: set[str] = {
    "quantidade", "qty", "qtd", "estoque", "stock",
    "margem", "margin", "lucro", "profit",
    "total", "subtotal", "soma", "sum",
    "obs", "observacao", "observação", "nota", "note",
    "imagem", "foto", "image", "photo",
    "ncm", "ncm fiscal",
    "linha", "col",
}

# Score mínimo para aceitar um match
MIN_SCORE_THRESHOLD = 0.60


@dataclass
class ScoredColumn:
    """Coluna com score de correspondência para um campo semântico."""

    col_index: int
    original_header: str
    normalized_header: str
    semantic_field: str
    score: float
    match_keyword: str = ""


def normalize_text(text: str) -> str:
    """
    Normaliza texto para comparação:
    - Remove acentos (ã→a, é→e, etc.)
    - Lowercase
    - Strip de espaços
    - Normaliza espaços múltiplos

    Ex: "  Descrição do Produto  " → "descricao do produto"
    """
    if not text:
        return ""
    # Normalização unicode: decompõe caracteres com acento
    text = unicodedata.normalize("NFKD", text)
    # Remove caracteres combinantes (os acentos separados)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Lowercase e strip
    text = text.lower().strip()
    # Normaliza espaços múltiplos
    text = " ".join(text.split())
    return text


def score_header(header: str, semantic_field: str) -> tuple[float, str]:
    """
    Calcula score de correspondência entre um cabeçalho e um campo semântico.

    Returns:
        (score, keyword_matched) — score 0.0-1.0 e a keyword que fez match
    """
    norm = normalize_text(header)
    if not norm:
        return 0.0, ""

    keywords = COLUMN_SYNONYMS.get(semantic_field, [])
    best_score = 0.0
    best_keyword = ""

    for kw in keywords:
        norm_kw = normalize_text(kw)
        if not norm_kw:
            continue

        score = 0.0

        # Exact match
        if norm == norm_kw:
            score = 1.0

        # Header começa com keyword (ex: "custo unitario" começa com "custo")
        elif norm.startswith(norm_kw) and len(norm_kw) >= 4:
            score = 0.88

        # Keyword é substring exata do header (ex: "descricao" em "descricao do produto")
        elif norm_kw in norm and len(norm_kw) >= 4:
            score = 0.82

        # Header é substring da keyword (ex: "prod" em "produto")
        elif norm in norm_kw and len(norm) >= 4:
            score = 0.72

        if score > best_score:
            best_score = score
            best_keyword = kw

    return best_score, best_keyword


def detect_columns(headers: list[str | None]) -> ColumnMappingResult:
    """
    Mapeia lista de cabeçalhos para campos semânticos.

    Algoritmo:
    1. Para cada cabeçalho, calcular score para cada campo semântico
    2. Para cada campo, escolher o cabeçalho com maior score
    3. Resolver conflitos: se dois campos querem a mesma coluna,
       o campo com maior score fica, o outro tenta próximo melhor

    Args:
        headers: Lista de cabeçalhos (índice = posição da coluna)

    Returns:
        ColumnMappingResult com índices mapeados
    """
    result = ColumnMappingResult()
    result.original_headers = [h or "" for h in headers]

    # Score matrix: [campo_semantico][col_index] = score
    all_scores: dict[str, list[ScoredColumn]] = {field: [] for field in COLUMN_SYNONYMS}
    unrecognized: list[str] = []

    for col_idx, header in enumerate(headers):
        if not header:
            continue

        header_str = str(header)
        norm = normalize_text(header_str)

        # Verificar se é coluna a ignorar
        if _should_ignore(norm):
            continue

        best_field = None
        best_score = 0.0
        best_kw = ""

        for field_name in COLUMN_SYNONYMS:
            score, kw = score_header(header_str, field_name)
            if score > best_score:
                best_score = score
                best_field = field_name
                best_kw = kw

        if best_field and best_score >= MIN_SCORE_THRESHOLD:
            all_scores[best_field].append(
                ScoredColumn(
                    col_index=col_idx,
                    original_header=header_str,
                    normalized_header=norm,
                    semantic_field=best_field,
                    score=best_score,
                    match_keyword=best_kw,
                )
            )
        else:
            unrecognized.append(header_str)

    # Ordenar candidatos por score DESC e escolher o melhor por campo
    assigned_columns: set[int] = set()
    field_mapping: dict[str, int | None] = {}

    for field_name in ["product_name", "cost", "sku", "category", "supplier"]:
        candidates = sorted(all_scores[field_name], key=lambda x: x.score, reverse=True)
        chosen = None

        for candidate in candidates:
            if candidate.col_index not in assigned_columns:
                chosen = candidate
                assigned_columns.add(candidate.col_index)
                result.scores[field_name] = candidate.score
                break

        field_mapping[field_name] = chosen.col_index if chosen else None

    # Atribuir ao result
    result.product_name = field_mapping.get("product_name")
    result.cost = field_mapping.get("cost")
    result.sku = field_mapping.get("sku")
    result.category = field_mapping.get("category")
    result.supplier = field_mapping.get("supplier")

    return result


def detect_header_row(rows: list[list], max_scan: int = 12) -> tuple[int, float]:
    """
    Detecta em qual linha estão os cabeçalhos escaneando as primeiras N linhas.

    Pontuação por linha:
    - +3 ponto por célula que bate com vocabulário de colunas
    - +1 ponto por célula que é string e não número
    - -2 por linha com maioria de células numéricas

    Args:
        rows: Linhas da planilha (lista de listas de valores)
        max_scan: Número máximo de linhas para escanear

    Returns:
        (header_row_index, confidence_score)
    """
    best_row_idx = 0
    best_score = -1.0

    for row_idx, row in enumerate(rows[:max_scan]):
        if not any(row):  # Linha vazia
            continue

        score = _score_as_header_row(row)

        if score > best_score:
            best_score = score
            best_row_idx = row_idx

    # Normalizar score para 0-1
    confidence = min(best_score / 10.0, 1.0) if best_score > 0 else 0.0

    return best_row_idx, confidence


def _score_as_header_row(row: list) -> float:
    """Pontua uma linha como candidata a cabeçalho."""
    score = 0.0
    non_empty = [c for c in row if c is not None and str(c).strip()]

    if not non_empty:
        return 0.0

    string_count = 0
    keyword_matches = 0

    for cell in non_empty:
        cell_str = str(cell)

        # Penaliza células muito longas (cabeçalhos são curtos)
        if len(cell_str) > 60:
            score -= 0.5
            continue

        # Bônus por ser string não-numérica
        try:
            float(cell_str.replace(",", ".").replace("R$", "").strip())
        except ValueError:
            string_count += 1
            score += 1.0

        # Bônus por bater com vocabulário
        for field_name in COLUMN_SYNONYMS:
            s, _ = score_header(cell_str, field_name)
            if s >= 0.70:
                keyword_matches += 1
                score += 3.0
                break

    # Penaliza se maioria são números (linha de dados, não cabeçalho)
    if string_count < len(non_empty) * 0.5:
        score -= 3.0

    # Bônus por ter múltiplas colunas reconhecidas
    if keyword_matches >= 2:
        score += 2.0

    return score


def _should_ignore(normalized_header: str) -> bool:
    """Verifica se um cabeçalho normalizado deve ser ignorado."""
    for ignore_kw in IGNORE_COLUMNS:
        norm_ignore = normalize_text(ignore_kw)
        if norm_ignore == normalized_header or norm_ignore in normalized_header:
            return True
    return False
