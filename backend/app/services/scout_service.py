"""
Scout Service — Ponto de entrada para extração e normalização de catálogos.

Responsabilidades:
1. Delegar para o parser correto por formato (XLSX, CSV, PDF)
2. Avaliar o resultado do parser (confiança, estatísticas)
3. Normalizar nomes via Claude API
4. Persistir produtos no banco

Mudanças desta versão:
- XLSX: agora usa XLSXParser com detecção robusta de colunas
- Retorna ParseResult com confiança, estatísticas e warnings
- Parsing PARTIAL não cancela o pipeline — continua com aviso
- Parsing FAILED cancela e marca catálogo como ERROR
"""

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.catalog import Catalog, FileType
from app.models.product import Product
from app.repositories.product_repo import ProductRepository
from app.services.parse_result import ParseConfidence, ParseResult

logger = logging.getLogger(__name__)


def parse_catalog(db: Session, catalog: Catalog) -> ParseResult:
    """
    Ponto de entrada principal do Scout Service.

    Detecta formato, parseia, normaliza e persiste os produtos.

    Returns:
        ParseResult com lista de Products salvos, confiança e estatísticas.
        ParseResult.products são objetos Product (já persistidos no banco).

    Raises:
        FileNotFoundError: se o arquivo não existir
        ValueError: se o formato não for suportado
    """
    file_path = Path(catalog.file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {catalog.file_path}")

    logger.info(
        "Scout: iniciando | catalog_id=%s | formato=%s | arquivo=%s",
        catalog.id, catalog.file_type, file_path.name
    )

    # ── Parsing por formato ───────────────────────────────────────────────────
    parse_result: ParseResult

    if catalog.file_type == FileType.XLSX:
        parse_result = _parse_xlsx(file_path)

    elif catalog.file_type == FileType.CSV:
        parse_result = _parse_csv(file_path)

    elif catalog.file_type == FileType.PDF:
        parse_result = _parse_pdf(file_path)

    else:
        raise ValueError(f"Formato não suportado: {catalog.file_type}")

    # ── Logar resultado do parsing ────────────────────────────────────────────
    logger.info("Scout: %s", parse_result.summary())

    for warning in parse_result.warnings:
        logger.warning("Scout [WARNING]: %s", warning)

    for error in parse_result.errors:
        logger.error("Scout [ERROR]: %s", error)

    # ── Falha crítica: nenhum produto extraído ────────────────────────────────
    if parse_result.confidence == ParseConfidence.FAILED:
        logger.error(
            "Scout: parsing FAILED | nenhum produto extraído | "
            "erros=%s", parse_result.errors
        )
        # Retorna o result com lista vazia para o task decidir o que fazer
        return parse_result

    # ── Normalização de nomes via IA ──────────────────────────────────────────
    raw_names = [p["raw_name"] for p in parse_result.products]
    normalized_names = _normalize_names(raw_names)

    for i, norm in enumerate(normalized_names):
        parse_result.products[i]["normalized_name"] = norm
        if norm:
            parse_result.stats.names_normalized += 1
        else:
            parse_result.stats.names_fallback += 1

    # ── Persistir produtos no banco ───────────────────────────────────────────
    if parse_result.products:
        saved_products = _persist_products(
            db=db,
            catalog=catalog,
            raw_products=parse_result.products,
        )
        # Substituir dicts pelos objetos Product persistidos
        parse_result.products = saved_products

        logger.info(
            "Scout: %d produtos persistidos | confidence=%s",
            len(saved_products), parse_result.confidence.value
        )
    else:
        parse_result.products = []
        parse_result.confidence = ParseConfidence.FAILED

    return parse_result


# ── Parsers por formato ───────────────────────────────────────────────────────

def _parse_xlsx(file_path: Path) -> ParseResult:
    """Delegação para o XLSXParser robusto."""
    from app.services.xlsx_parser import parse_xlsx_catalog
    return parse_xlsx_catalog(file_path)


def _parse_csv(file_path: Path) -> ParseResult:
    """
    Parser CSV básico — robusto o suficiente para MVP.

    Tenta múltiplos separadores e encodings.
    Usa a mesma lógica de column_detector para flexibilidade.
    """
    from decimal import Decimal

    import pandas as pd

    from app.services.column_detector import detect_columns
    from app.services.parse_result import ParseResult, ParseStats
    from app.services.price_normalizer import is_valid_cost, normalize_price

    result = ParseResult()
    stats = ParseStats()

    # Tentar diferentes combinações de separador e encoding
    df = None
    for sep in [",", ";", "\t", "|"]:
        for enc in ["utf-8", "utf-8-sig", "latin-1", "iso-8859-1", "cp1252"]:
            try:
                test_df = pd.read_csv(file_path, sep=sep, encoding=enc, nrows=5)
                if len(test_df.columns) >= 2:  # Pelo menos 2 colunas
                    df = pd.read_csv(file_path, sep=sep, encoding=enc, dtype=str)
                    break
            except Exception:
                continue
        if df is not None:
            break

    if df is None or df.empty:
        result.add_error("Não foi possível ler o CSV. Verifique o formato e encoding.")
        result.confidence = ParseConfidence.FAILED
        return result

    headers = list(df.columns)
    col_mapping = detect_columns(headers)
    result.column_mapping = col_mapping

    if not col_mapping.has_required_columns:
        result.add_error(
            f"Colunas obrigatórias não encontradas no CSV. "
            f"Cabeçalhos: {headers}"
        )
        result.confidence = ParseConfidence.FAILED
        return result

    products = []
    for _, row in df.iterrows():
        stats.total_rows_scanned += 1

        name_col_name = headers[col_mapping.product_name] if col_mapping.product_name is not None else None
        cost_col_name = headers[col_mapping.cost] if col_mapping.cost is not None else None

        raw_name = str(row[name_col_name]).strip() if name_col_name else None
        cost_str = str(row[cost_col_name]).strip() if cost_col_name else None

        if not raw_name or raw_name.lower() in ("nan", "none", ""):
            stats.skipped_invalid_name += 1
            continue

        cost = normalize_price(cost_str)
        if not is_valid_cost(cost):
            stats.skipped_invalid_cost += 1
            continue

        sku_col_name = headers[col_mapping.sku] if col_mapping.sku is not None else None
        cat_col_name = headers[col_mapping.category] if col_mapping.category is not None else None
        sup_col_name = headers[col_mapping.supplier] if col_mapping.supplier is not None else None

        def safe_str(val):
            s = str(val).strip() if val else None
            return s if s and s.lower() not in ("nan", "none", "") else None

        products.append({
            "raw_name": raw_name,
            "cost": cost,
            "sku": safe_str(row[sku_col_name]) if sku_col_name else None,
            "category": safe_str(row[cat_col_name]) if cat_col_name else None,
            "supplier": safe_str(row[sup_col_name]) if sup_col_name else None,
            "currency": "BRL",
        })
        stats.valid_products += 1

    result.products = products
    result.stats = stats
    result.confidence = (
        ParseConfidence.RELIABLE if stats.success_rate >= 0.80
        else ParseConfidence.PARTIAL if stats.valid_products > 0
        else ParseConfidence.FAILED
    )

    return result


def _parse_pdf(file_path: Path) -> ParseResult:
    """
    Parser PDF básico — extração de texto simples.
    Limitado a PDFs com texto digital (não escaneados).
    Fase 2: extração inteligente com Claude API.
    """
    from app.services.parse_result import ParseResult

    result = ParseResult()
    result.add_warning(
        "PDF parsing é limitado no MVP. "
        "Para melhores resultados, converta para XLSX antes de enviar."
    )

    try:
        import fitz  # PyMuPDF
    except ImportError:
        result.add_error("PyMuPDF não instalado. PDF não pode ser processado.")
        result.confidence = ParseConfidence.FAILED
        return result

    doc = fitz.open(str(file_path))
    all_text = "".join(page.get_text("text") for page in doc)
    doc.close()

    if not all_text.strip():
        result.add_error(
            "PDF não contém texto extraível (possivelmente escaneado). "
            "Suporte a PDF escaneado está planejado para Fase 2."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    # TODO Fase 2: usar Claude API para estruturar o texto do PDF
    result.add_error(
        "Parser de PDF ainda não implementado para extração estruturada. "
        "Converta o catálogo para XLSX."
    )
    result.confidence = ParseConfidence.FAILED
    return result


# ── Utilitários ───────────────────────────────────────────────────────────────

def _normalize_names(raw_names: list[str]) -> list[str | None]:
    """
    Normaliza nomes via Claude API.
    Fallback gracioso se a API falhar — usa None (raw_name será usado na busca ML).
    """
    from app.integrations.claude import normalize_product_names

    try:
        return normalize_product_names(raw_names)
    except Exception as exc:
        logger.warning(
            "Scout: normalização Claude falhou (%s) — usando nomes originais",
            exc
        )
        return [None] * len(raw_names)


def _persist_products(
    db: Session,
    catalog: Catalog,
    raw_products: list[dict],
) -> list[Product]:
    """Persiste lista de produtos no banco via bulk insert."""
    from app.models.product import Product as ProductModel

    repo = ProductRepository(db)

    product_objects = [
        ProductModel(
            catalog_id=catalog.id,
            user_id=catalog.user_id,
            raw_name=p["raw_name"],
            normalized_name=p.get("normalized_name"),
            sku=p.get("sku"),
            category=p.get("category"),
            supplier=p.get("supplier"),
            cost=p["cost"],
            currency=p.get("currency", "BRL"),
        )
        for p in raw_products
    ]

    return repo.bulk_create(product_objects)
