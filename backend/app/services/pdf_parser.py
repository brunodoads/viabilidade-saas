"""
PDF Parser — Extração Híbrida de Catálogos PDF.

Estratégia de dois estágios para PDFs mistos (digital + escaneado):

Estágio 1 — pdfplumber:
  Extrai tabelas estruturadas de PDFs com texto digital.
  Funciona bem para catálogos gerados por Excel/Word.
  Falha silenciosamente se não houver tabelas formais.

Estágio 2 — Claude Vision API:
  Converte cada página em imagem PNG e envia ao Claude para extração.
  Cobre PDFs escaneados, layouts complexos sem tabelas, e catálogos
  com imagens de produtos com preços ao lado.

Fallback: se ambos falharem, retorna FAILED com diagnóstico completo.

Custo estimado Claude Vision:
  ~$0.003 por página com claude-3-5-sonnet (input ~1500 tokens/imagem)
  Limite: MAX_VISION_PAGES=10 por catálogo para controlar custo no MVP.
"""

from __future__ import annotations

import base64
import json
import logging
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

# Limite de páginas enviadas ao Claude Vision (custo/tempo)
MAX_VISION_PAGES = 10

# Modelo com melhor capacidade de visão/OCR
VISION_MODEL = "claude-3-5-sonnet-20241022"


def parse_pdf_catalog(file_path: Path) -> "ParseResult":
    """
    Ponto de entrada: parseia um catálogo PDF usando estratégia híbrida.

    Tenta pdfplumber primeiro (rápido, barato).
    Se falhar, usa Claude Vision (robusto, cobre escaneados).

    Returns:
        ParseResult com produtos, confiança e estatísticas.
    """
    from app.services.parse_result import ParseConfidence, ParseResult

    logger.info("PDF parser iniciando | arquivo=%s", file_path.name)

    # ── Estágio 1: pdfplumber ───────────────────────────────────────────────────
    result = _try_pdfplumber(file_path)

    if result.confidence != ParseConfidence.FAILED:
        logger.info(
            "PDF via pdfplumber OK | confiança=%s | %d produtos",
            result.confidence.value, len(result.products),
        )
        return result

    pdfplumber_errors = list(result.errors)

    # ── Estágio 2: Claude Vision ────────────────────────────────────────────────
    logger.info(
        "pdfplumber falhou (%s) — tentando Claude Vision",
        "; ".join(pdfplumber_errors),
    )

    result = _try_claude_vision(file_path)

    if result.confidence == ParseConfidence.FAILED and pdfplumber_errors:
        for err in pdfplumber_errors:
            prefixed = f"[pdfplumber] {err}"
            if prefixed not in result.errors:
                result.add_error(prefixed)

    if result.confidence != ParseConfidence.FAILED:
        logger.info(
            "PDF via Claude Vision OK | confiança=%s | %d produtos",
            result.confidence.value, len(result.products),
        )

    return result


# ── Estágio 1: pdfplumber ─────────────────────────────────────────────────────


def _try_pdfplumber(file_path: Path) -> "ParseResult":
    """
    Extrai tabelas de PDFs estruturados com texto digital.

    Passa pelos headers de cada tabela, usa column_detector para identificar
    colunas de produto/custo, e parseia linha a linha igual ao CSV.
    """
    from app.services.column_detector import detect_columns
    from app.services.parse_result import (
        ColumnMappingResult,
        ParseConfidence,
        ParseResult,
        ParseStats,
    )
    from app.services.price_normalizer import is_valid_cost, normalize_price

    result = ParseResult()
    stats = ParseStats()

    try:
        import pdfplumber
    except ImportError:
        result.add_error(
            "pdfplumber não instalado. "
            "Adicione 'pdfplumber' ao pyproject.toml e redeploye."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    all_tables: list[list[list]] = []

    try:
        with pdfplumber.open(str(file_path)) as pdf:
            stats.total_sheets = len(pdf.pages)
            for page in pdf.pages:
                page_tables = page.extract_tables()
                if page_tables:
                    all_tables.extend(page_tables)
    except Exception as exc:
        result.add_error(f"Erro ao abrir PDF com pdfplumber: {exc}")
        result.confidence = ParseConfidence.FAILED
        return result

    if not all_tables:
        result.add_error(
            "pdfplumber não encontrou tabelas no PDF. "
            "PDF pode ser escaneado ou ter layout sem tabelas formais."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    products_found: list[dict] = []
    best_col_mapping: ColumnMappingResult | None = None

    for table in all_tables:
        if not table or len(table) < 2:
            continue

        # Primeira linha como cabeçalho
        headers = [str(h).strip() if h else "" for h in table[0]]

        col_mapping = detect_columns(headers)
        if not col_mapping.has_required_columns:
            continue

        best_col_mapping = col_mapping
        data_rows = table[1:]

        for row in data_rows:
            stats.total_rows_scanned += 1
            if not row:
                stats.skipped_empty += 1
                continue

            def _cell(idx: int | None) -> str | None:
                if idx is None or idx >= len(row):
                    return None
                val = row[idx]
                if val is None:
                    return None
                s = str(val).strip()
                return s if s and s.lower() not in ("none", "nan", "") else None

            raw_name = _cell(col_mapping.product_name)
            cost_str = _cell(col_mapping.cost)

            if not raw_name:
                stats.skipped_invalid_name += 1
                continue

            cost = normalize_price(cost_str)
            if not is_valid_cost(cost):
                stats.skipped_invalid_cost += 1
                continue

            products_found.append({
                "raw_name": raw_name,
                "cost": cost,
                "sku": _cell(col_mapping.sku),
                "category": _cell(col_mapping.category),
                "supplier": _cell(col_mapping.supplier),
                "currency": "BRL",
            })
            stats.valid_products += 1

        if products_found:
            break  # Encontrou produtos — não precisa tentar outras tabelas

    if not products_found:
        result.add_error(
            "pdfplumber encontrou tabelas mas nenhum produto válido (nome + custo). "
            "Colunas obrigatórias podem ter nomes não reconhecidos."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    result.products = products_found
    result.stats = stats
    if best_col_mapping:
        result.column_mapping = best_col_mapping

    result.confidence = (
        ParseConfidence.RELIABLE if stats.success_rate >= 0.80
        else ParseConfidence.PARTIAL if stats.valid_products > 0
        else ParseConfidence.FAILED
    )

    return result


# ── Estágio 2: Claude Vision ──────────────────────────────────────────────────

_VISION_SYSTEM_PROMPT = """Você é um extrator especializado em catálogos de produtos de importadoras e distribuidoras brasileiras.

Sua tarefa é analisar a imagem de uma página de catálogo e extrair TODOS os produtos visíveis.

Para cada produto, extraia:
- raw_name: nome do produto exatamente como aparece no catálogo (obrigatório)
- cost: preço de custo como número decimal (obrigatório — use null se não encontrar)
- sku: código/referência do produto (opcional, null se não encontrar)
- category: categoria ou grupo do produto (opcional, null se não encontrar)
- supplier: nome do fornecedor ou marca (opcional, null se não encontrar)

Regras:
1. Retorne APENAS um JSON array de objetos, sem texto antes ou depois
2. Se a página não tiver produtos, retorne []
3. Preços devem ser números: 49.90 (não "R$ 49,90")
4. Converta vírgula decimal BR: "49,90" → 49.90 e "1.234,56" → 1234.56
5. Ignore totais, subtotais, cabeçalhos de seção e rodapés de página
6. Inclua todos os produtos, mesmo os parcialmente visíveis

Exemplo de resposta:
[
  {"raw_name": "Kit LED 12V 5W Bivolt", "cost": 23.50, "sku": "LED-001", "category": "Iluminação", "supplier": null},
  {"raw_name": "Frasco Plástico 500ml com Tampa", "cost": 8.90, "sku": null, "category": null, "supplier": null}
]"""


def _try_claude_vision(file_path: Path) -> "ParseResult":
    """
    Extrai produtos de PDFs via Claude Vision API.

    Converte cada página em PNG (150 DPI) e envia ao Claude Sonnet
    para extração estruturada em JSON.
    """
    from app.core.config import settings
    from app.services.parse_result import ParseConfidence, ParseResult, ParseStats
    from app.services.price_normalizer import is_valid_cost

    result = ParseResult()
    stats = ParseStats()

    if not settings.CLAUDE_API_KEY:
        result.add_error(
            "CLAUDE_API_KEY não configurada — Claude Vision não disponível. "
            "Configure a variável de ambiente para processar PDFs escaneados."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    # Converter páginas em imagens PNG
    page_images = _pdf_pages_to_images(file_path, max_pages=MAX_VISION_PAGES)

    if not page_images:
        result.add_error(
            "Não foi possível converter páginas do PDF em imagens (PyMuPDF necessário)."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    if len(page_images) == MAX_VISION_PAGES:
        result.add_warning(
            f"PDF tem muitas páginas — processando apenas as primeiras {MAX_VISION_PAGES} "
            "para controlar custo. Considere dividir o catálogo."
        )

    logger.info(
        "Claude Vision | %d páginas | modelo=%s | arquivo=%s",
        len(page_images), VISION_MODEL, file_path.name,
    )

    import anthropic
    client = anthropic.Anthropic(api_key=settings.CLAUDE_API_KEY)

    all_products: list[dict] = []
    seen_names: set[str] = set()

    for page_idx, image_bytes in enumerate(page_images):
        page_products = _extract_products_from_page(
            client=client,
            image_bytes=image_bytes,
            page_number=page_idx + 1,
        )
        stats.total_rows_scanned += len(page_products)

        for product in page_products:
            raw_name = str(product.get("raw_name", "")).strip()
            if not raw_name:
                stats.skipped_invalid_name += 1
                continue

            # Deduplicação por nome exato (PDFs com rodapés repetidos)
            name_key = raw_name.lower()
            if name_key in seen_names:
                stats.skipped_duplicate_sku += 1
                continue
            seen_names.add(name_key)

            # Normalizar custo
            cost_raw = product.get("cost")
            cost: Decimal | None = None
            if cost_raw is not None:
                try:
                    cost = Decimal(str(cost_raw))
                except Exception:
                    cost = None

            if not is_valid_cost(cost):
                stats.skipped_invalid_cost += 1
                continue

            def _safe(val) -> str | None:
                if val is None:
                    return None
                s = str(val).strip()
                return s if s and s.lower() not in ("none", "null", "nan", "") else None

            all_products.append({
                "raw_name": raw_name,
                "cost": cost,
                "sku": _safe(product.get("sku")),
                "category": _safe(product.get("category")),
                "supplier": _safe(product.get("supplier")),
                "currency": "BRL",
            })
            stats.valid_products += 1

    if not all_products:
        result.add_error(
            "Claude Vision não extraiu nenhum produto válido. "
            "Verifique se o PDF contém catálogo com produtos e preços."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    result.products = all_products
    result.stats = stats
    result.confidence = (
        ParseConfidence.RELIABLE if stats.success_rate >= 0.80
        else ParseConfidence.PARTIAL if stats.valid_products > 0
        else ParseConfidence.FAILED
    )
    result.add_warning(
        f"Produtos extraídos via Claude Vision de {len(page_images)} página(s). "
        "Revise os resultados — OCR pode ter imprecisões."
    )

    return result


def _pdf_pages_to_images(file_path: Path, max_pages: int = 10) -> list[bytes]:
    """
    Converte páginas de PDF em imagens PNG usando PyMuPDF (fitz).

    Resolução 150 DPI: boa qualidade para OCR sem exceder limites de tokens.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF (fitz) não instalado — necessário para Claude Vision")
        return []

    images: list[bytes] = []
    try:
        doc = fitz.open(str(file_path))
        pages_to_process = min(len(doc), max_pages)

        for page_idx in range(pages_to_process):
            page = doc[page_idx]
            # 150/72 ≈ 2.08x scale → ~150 DPI
            mat = fitz.Matrix(150 / 72, 150 / 72)
            pixmap = page.get_pixmap(matrix=mat)
            images.append(pixmap.tobytes("png"))

        doc.close()
        logger.debug("PDF→imagens | %d páginas convertidas", len(images))

    except Exception as exc:
        logger.error("Erro ao converter PDF para imagens: %s", exc, exc_info=True)

    return images


def _extract_products_from_page(
    client,
    image_bytes: bytes,
    page_number: int,
) -> list[dict]:
    """
    Envia uma página ao Claude Vision e parseia o JSON de produtos retornado.

    Trata erros de JSON e da API silenciosamente (página retorna lista vazia).
    """
    import anthropic

    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    try:
        response = client.messages.create(
            model=VISION_MODEL,
            max_tokens=4096,
            system=_VISION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Extraia todos os produtos desta página "
                                f"(página {page_number} do catálogo)."
                            ),
                        },
                    ],
                }
            ],
        )

        raw_text = response.content[0].text.strip()

        # Limpar markdown (```json ... ```) se presente
        if "```" in raw_text:
            parts = raw_text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    raw_text = part[4:].strip()
                    break
                if part.startswith("["):
                    raw_text = part
                    break

        products = json.loads(raw_text)

        if not isinstance(products, list):
            logger.warning(
                "Claude Vision p%d: resposta não é lista — tipo=%s",
                page_number, type(products).__name__,
            )
            return []

        logger.info("Claude Vision | página=%d | %d produtos", page_number, len(products))
        return products

    except json.JSONDecodeError as exc:
        logger.error("Claude Vision p%d: JSON decode error: %s", page_number, exc)
        return []
    except anthropic.APIError as exc:
        logger.error("Claude Vision p%d: API error: %s", page_number, exc)
        return []
    except Exception as exc:
        logger.error(
            "Claude Vision p%d: erro inesperado: %s", page_number, exc, exc_info=True
        )
        return []
