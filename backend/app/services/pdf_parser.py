"""
PDF Parser - Extracao Hibrida de Catalogos PDF.

Estagio 1a - pdfplumber tabelas estruturadas (colunas separadas nome/preco)
Estagio 1b - pdfplumber texto concatenado (celula unica por produto, ex: EXBOM)
Estagio 2  - Claude Vision (PDFs escaneados ou layouts complexos)
"""

from __future__ import annotations

import base64
import json
import logging
import re
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_VISION_PAGES = 10
VISION_MODEL = "claude-3-5-sonnet-20241022"

# Regex para preco: "PRECO: R$ 33.50" ou "R$ 16,70"
_PRICE_RE = re.compile(
    r'(?:PRE[CcCc]O\s*[:.]?\s*)?R\$\s*([\d]{1,6}(?:[.,]\d{1,3})*)',
    re.IGNORECASE | re.UNICODE,
)
# SKU numerico no inicio
_SKU_RE = re.compile(r'^(\d{3,8})\s*[/\-]?\s*')

# Fragmentos que indicam celula de cabecalho/rodape/endereco (nao produto)
_NON_PRODUCT_FRAGMENTS = (
    'data:', 'folhas:', 'distribuidor', 'fone/fax', 'fone:(', 'fax:(',
    'republica', 'sao paulo', 'rua ', 'av.', 'avenida', 'proximo',
    'emissao', 'pagina ', 'page ', 'tabela de preco', 'imp. data',
)


def parse_pdf_catalog(file_path: Path):
    from app.services.parse_result import ParseConfidence, ParseResult

    logger.info("PDF parser iniciando | arquivo=%s", file_path.name)

    result = _try_pdfplumber(file_path)

    if result.confidence != ParseConfidence.FAILED:
        logger.info(
            "PDF via pdfplumber OK | confianca=%s | %d produtos",
            result.confidence.value, len(result.products),
        )
        return result

    pdfplumber_errors = list(result.errors)
    logger.info("pdfplumber falhou - tentando Claude Vision")

    result = _try_claude_vision(file_path)

    if result.confidence == ParseConfidence.FAILED and pdfplumber_errors:
        for err in pdfplumber_errors:
            prefixed = "[pdfplumber] " + err
            if prefixed not in result.errors:
                result.add_error(prefixed)

    if result.confidence != ParseConfidence.FAILED:
        logger.info(
            "PDF via Claude Vision OK | confianca=%s | %d produtos",
            result.confidence.value, len(result.products),
        )

    return result


# ---------------------------------------------------------------------------
# Estagio 1: pdfplumber
# ---------------------------------------------------------------------------

def _try_pdfplumber(file_path: Path):
    from app.services.column_detector import detect_columns, detect_header_row
    from app.services.parse_result import ParseConfidence, ParseResult, ParseStats
    from app.services.price_normalizer import is_valid_cost, normalize_price

    result = ParseResult()
    stats = ParseStats()

    try:
        import pdfplumber
    except ImportError:
        result.add_error("pdfplumber nao instalado.")
        result.confidence = ParseConfidence.FAILED
        return result

    all_tables = []

    try:
        with pdfplumber.open(str(file_path)) as pdf:
            stats.total_sheets = len(pdf.pages)
            for page in pdf.pages:
                page_tables = page.extract_tables()
                if page_tables:
                    all_tables.extend(page_tables)
    except Exception as exc:
        result.add_error("Erro ao abrir PDF com pdfplumber: " + str(exc))
        result.confidence = ParseConfidence.FAILED
        return result

    if not all_tables:
        result.add_error(
            "pdfplumber nao encontrou tabelas no PDF. "
            "PDF pode ser escaneado ou ter layout sem tabelas formais."
        )
        result.confidence = ParseConfidence.FAILED
        return result

    # --- 1a: tabelas com colunas separadas (nome + preco em colunas distintas)
    products_found = []
    best_col_mapping = None

    for table in all_tables:
        if not table or len(table) < 2:
            continue

        for try_row in _candidate_header_rows(table):
            headers = [str(h).strip() if h else "" for h in table[try_row]]
            col_mapping = detect_columns(headers)

            if not col_mapping.has_required_columns:
                continue

            best_col_mapping = col_mapping
            data_rows = table[try_row + 1:]

            for row in data_rows:
                stats.total_rows_scanned += 1
                if not row:
                    stats.skipped_empty += 1
                    continue

                def _cell(idx, _row=row):
                    if idx is None or idx >= len(_row):
                        return None
                    val = _row[idx]
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

            break  # achou cabecalho valido nesta tabela

        if products_found:
            break

    if products_found:
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

    # --- 1b: celulas individuais com texto concatenado (formato EXBOM/similar)
    # Cada celula pode conter: "SKU / MARCA / MODELO: XXX Nome do produto (QTY) PRECO: R$ X.XX"
    # Coletar TODAS as celulas individualmente (nao concatenar colunas de uma linha)
    all_cells = []
    for table in all_tables:
        for row in table:
            if not row:
                continue
            for cell in row:
                if cell and str(cell).strip():
                    all_cells.append(str(cell).strip())

    if all_cells:
        text_products = _parse_concatenated_cells(all_cells)
        if text_products:
            stats.valid_products = len(text_products)
            stats.total_rows_scanned = len(all_cells)
            result.products = text_products
            result.stats = stats
            result.add_warning(
                "PDF usa formato de celula unica concatenada (sem colunas separadas). "
                "Extrai via parser de texto com regex. Confira os nomes no dashboard."
            )
            result.confidence = (
                ParseConfidence.RELIABLE if len(text_products) >= 5
                else ParseConfidence.PARTIAL
            )
            return result

    # Tudo falhou: reportar cabecalhos para diagnostico (limitado para nao poluir)
    sample_cells = all_cells[:5] if all_cells else []
    sample_str = ", ".join('"' + c[:50] + '"' for c in sample_cells) if sample_cells else "nenhum"

    result.add_error(
        "pdfplumber encontrou tabelas mas nenhum produto valido (nome + custo). "
        "Amostra de celulas: [" + sample_str + "]. "
        "Reporte o formato para adicionar suporte."
    )
    result.confidence = ParseConfidence.FAILED
    return result


def _candidate_header_rows(table: list) -> list:
    from app.services.column_detector import detect_header_row
    header_row_idx, _ = detect_header_row(table, max_scan=min(5, len(table)))
    candidates = [header_row_idx]
    if 0 not in candidates:
        candidates.append(0)
    return candidates


def _parse_concatenated_cells(cells: list) -> list:
    """
    Parser para PDFs onde cada celula contem todas as infos do produto.

    Formato EXBOM:
      "04763/ EXBOM / MODELO: CS-M31BT-MAX/ Caixa de Som ... ( 30 PCS / CX ) PRECO: R$ 33.50"
    Formato generico:
      "Produto XYZ - R$ 49,90 - Cod: 123"
    """
    products = []
    seen = set()

    for cell in cells:
        cell = cell.strip()
        if not cell or len(cell) < 15:
            continue

        cell_lower = cell.lower()

        # Ignorar celulas de rodape/endereco/cabecalho de secao
        if any(frag in cell_lower for frag in _NON_PRODUCT_FRAGMENTS):
            continue

        # Obrigatorio: ter algum valor monetario
        price_match = _PRICE_RE.search(cell)
        if not price_match:
            continue

        # Converter preco para Decimal
        price_str = price_match.group(1).replace(',', '.')
        # Tratar milhar: "1.234" (3 digitos apos ponto) -> "1234"
        parts = price_str.split('.')
        if len(parts) == 2 and len(parts[1]) == 3:
            price_str = ''.join(parts)
        try:
            cost = Decimal(price_str)
        except Exception:
            continue

        if cost <= 0 or cost > 100000:
            continue

        # Extrair SKU do inicio (sequencia numerica)
        sku_match = _SKU_RE.match(cell)
        sku = sku_match.group(1) if sku_match else None

        # Construir nome limpo
        name = cell

        # 1. Remover SKU inicial
        if sku_match:
            name = name[sku_match.end():]

        # 2a. Remover "/ MARCA /" no meio do texto (ex: "/ EXBOM /")
        name = re.sub(r'/\s*[A-Z][A-Z0-9\-&\.\s]{2,25}\s*/', ' ', name)
        # 2b. Remover "MARCA /" no INICIO quando barra foi consumida pelo SKU regex
        #     Ex: "EXBOM / MODELO: ..." -> remove "EXBOM /"
        name = re.sub(r'^[A-Z][A-Z0-9\-&\.]{1,20}\s*/\s*', '', name.strip())

        # 3. Remover "MODELO: XXXX" incluindo codigo alfanumerico com hifen
        name = re.sub(r'\bMODELO\s*:\s*[\w][\w\-/]*\s*', ' ', name, flags=re.IGNORECASE)

        # 4. Remover cor/variante isolada no inicio: "/ VERMELHO", "/ PRETO"
        name = re.sub(r'^[\s/\-]+([A-Z]{4,})\s+', r'\1 ', name.strip())

        # 5. Remover quantidades: "( 30 PCS / CX )", "(100 UN)", etc.
        name = re.sub(
            r'\(\s*\d+\s*(?:PCS|UN|PC|KIT|CX|BX|DZ|PARES?)\s*/?\s*\w*\s*\)',
            '', name, flags=re.IGNORECASE
        )

        # 6. Remover tudo a partir de PRECO/R$ em diante
        name = re.sub(r'\bPRE[CcCc]O\b.*', '', name, flags=re.IGNORECASE | re.DOTALL | re.UNICODE)
        name = re.sub(r'R\$\s*[\d.,]+.*', '', name, flags=re.DOTALL)

        # 7. Limpar separadores sobrando no inicio/fim
        name = re.sub(r'\s+', ' ', name)
        name = re.sub(r'^[\s/|\-]+', '', name)
        name = re.sub(r'[\s/|\-]+$', '', name)
        name = name.strip()

        if len(name) < 5:
            continue

        # Deduplicar por nome normalizado
        name_key = re.sub(r'\s+', ' ', name.lower().strip())
        if name_key in seen:
            continue
        seen.add(name_key)

        # Detectar fornecedor/marca mencionado na celula
        supplier = None
        sup_match = re.search(
            r'\b(EXBOM|NEXT|JBL|PHILIPS|MULTILASER|INTELBRAS|MOTOROLA|SAMSUNG|XIAOMI|LENOVO)\b',
            cell, re.IGNORECASE
        )
        if sup_match:
            supplier = sup_match.group(1).upper()

        products.append({
            "raw_name": name,
            "cost": cost,
            "sku": sku,
            "category": None,
            "supplier": supplier,
            "currency": "BRL",
        })

    logger.info(
        "Parser concatenado | %d celulas | %d produtos extraidos",
        len(cells), len(products),
    )
    return products


# ---------------------------------------------------------------------------
# Estagio 2: Claude Vision
# ---------------------------------------------------------------------------

_VISION_SYSTEM_PROMPT = (
    "Voce e um extrator especializado em catalogos de produtos de importadoras "
    "e distribuidoras brasileiras.\n\n"
    "Sua tarefa e analisar a imagem de uma pagina de catalogo e extrair TODOS os produtos visiveis.\n\n"
    "Para cada produto, extraia:\n"
    "- raw_name: nome do produto exatamente como aparece (obrigatorio)\n"
    "- cost: QUALQUER valor monetario visivel associado ao produto - pode ser "
    "preco, preco de venda, valor, vlr, vl, tabela, atacado, custo ou qualquer "
    "campo numerico com RS. Use o primeiro valor monetario que encontrar. "
    "Se houver multiplos precos, use o MENOR. Null apenas se nao houver nenhum numero.\n"
    "- sku: codigo, referencia, cod, ref, SKU do produto (opcional, null se nao encontrar)\n"
    "- category: categoria, grupo, linha, familia do produto (opcional, null)\n"
    "- supplier: fornecedor, fabricante, marca (opcional, null)\n\n"
    "Regras:\n"
    "1. Retorne APENAS um JSON array de objetos, sem texto antes ou depois\n"
    "2. Se a pagina nao tiver produtos, retorne []\n"
    "3. Precos devem ser numeros Python: 49.90 (NAO strings)\n"
    "4. Converta formato BR: 49,90 -> 49.90 e 1.234,56 -> 1234.56\n"
    "5. Ignore linhas de total, subtotal, cabecalhos de secao e rodapes\n"
    "6. Inclua todos os produtos visiveis, mesmo os parcialmente cortados\n\n"
    'Exemplo: [{"raw_name": "Kit LED 12V 5W", "cost": 23.50, "sku": "LED-001", "category": "Iluminacao", "supplier": null}]'
)


def _try_claude_vision(file_path: Path):
    from app.core.config import settings
    from app.services.parse_result import ParseConfidence, ParseResult, ParseStats
    from app.services.price_normalizer import is_valid_cost

    result = ParseResult()
    stats = ParseStats()

    if not settings.CLAUDE_API_KEY:
        result.add_error("CLAUDE_API_KEY nao configurada - Claude Vision nao disponivel.")
        result.confidence = ParseConfidence.FAILED
        return result

    page_images = _pdf_pages_to_images(file_path, max_pages=MAX_VISION_PAGES)

    if not page_images:
        result.add_error("Nao foi possivel converter paginas do PDF em imagens.")
        result.confidence = ParseConfidence.FAILED
        return result

    if len(page_images) == MAX_VISION_PAGES:
        result.add_warning(
            "PDF tem muitas paginas - processando apenas as primeiras {}.".format(MAX_VISION_PAGES)
        )

    logger.info(
        "Claude Vision | %d paginas | modelo=%s | arquivo=%s",
        len(page_images), VISION_MODEL, file_path.name,
    )

    import anthropic
    client = anthropic.Anthropic(api_key=settings.CLAUDE_API_KEY)

    all_products = []
    seen_names = set()

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

            name_key = raw_name.lower()
            if name_key in seen_names:
                stats.skipped_duplicate_sku += 1
                continue
            seen_names.add(name_key)

            cost_raw = product.get("cost")
            cost = None
            if cost_raw is not None:
                try:
                    cost = Decimal(str(cost_raw))
                except Exception:
                    cost = None

            if not is_valid_cost(cost):
                stats.skipped_invalid_cost += 1
                continue

            def _safe(val):
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
            "Claude Vision nao extraiu nenhum produto valido. "
            "Verifique se o PDF contem catalogo com produtos e precos."
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
        "Produtos extraidos via Claude Vision de {} pagina(s). "
        "Revise os resultados - OCR pode ter imprecisoes.".format(len(page_images))
    )
    return result


def _pdf_pages_to_images(file_path: Path, max_pages: int = 10) -> list:
    try:
        import fitz
    except ImportError:
        logger.error("PyMuPDF (fitz) nao instalado")
        return []

    images = []
    try:
        doc = fitz.open(str(file_path))
        pages_to_process = min(len(doc), max_pages)
        for page_idx in range(pages_to_process):
            page = doc[page_idx]
            mat = fitz.Matrix(150 / 72, 150 / 72)
            pixmap = page.get_pixmap(matrix=mat)
            images.append(pixmap.tobytes("png"))
        doc.close()
        logger.debug("PDF->imagens | %d paginas convertidas", len(images))
    except Exception as exc:
        logger.error("Erro ao converter PDF para imagens: %s", exc, exc_info=True)

    return images


def _extract_products_from_page(client, image_bytes: bytes, page_number: int) -> list:
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
                            "text": "Extraia todos os produtos desta pagina (pagina {} do catalogo).".format(page_number),
                        },
                    ],
                }
            ],
        )

        raw_text = response.content[0].text.strip()

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
            logger.warning("Claude Vision p%d: resposta nao e lista", page_number)
            return []

        logger.info("Claude Vision | pagina=%d | %d produtos", page_number, len(products))
        return products

    except json.JSONDecodeError as exc:
        logger.error("Claude Vision p%d: JSON decode error: %s", page_number, exc)
        return []
    except anthropic.APIError as exc:
        logger.error("Claude Vision p%d: API error: %s", page_number, exc)
        return []
    except Exception as exc:
        logger.error("Claude Vision p%d: erro inesperado: %s", page_number, exc, exc_info=True)
        return []
