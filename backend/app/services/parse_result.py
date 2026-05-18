"""
Tipos de resultado do pipeline de parsing de catálogos.

ParseResult é o contrato entre o xlsx_parser e o scout_service.
Contém tudo que o pipeline precisa saber: produtos, confiança, estatísticas, avisos.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ParseConfidence(str, Enum):
    """
    Nível de confiança do parsing de um catálogo.

    Baseado em:
    - Colunas obrigatórias encontradas (nome e custo)
    - Taxa de sucesso de linhas válidas
    - Qualidade dos dados extraídos

    RELIABLE  → tudo encontrado, >80% linhas OK
    PARTIAL   → colunas encontradas, 20-80% linhas OK (ou colunas opcionais faltando)
    FAILED    → colunas obrigatórias não encontradas ou <20% linhas OK
    """

    RELIABLE = "RELIABLE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass
class ParseStats:
    """Estatísticas detalhadas do processo de parsing linha a linha."""

    # Contagem de linhas
    total_rows_scanned: int = 0
    valid_products: int = 0

    # Linhas ignoradas (categorizadas por motivo)
    skipped_empty: int = 0          # Linha completamente vazia
    skipped_invalid_name: int = 0   # Nome ausente, muito curto ou parece número
    skipped_invalid_cost: int = 0   # Custo ausente, zero, negativo ou não parseável
    skipped_subtotal: int = 0       # Linha de total/subtotal detectada
    skipped_duplicate_sku: int = 0  # SKU duplicado dentro do mesmo catálogo
    skipped_header_repeat: int = 0  # Linha que repete o cabeçalho

    # Qualidade dos dados
    cost_parse_errors: int = 0      # Tentativas de parse de preço que falharam
    names_normalized: int = 0       # Nomes normalizados com sucesso pela IA
    names_fallback: int = 0         # Nomes que usaram raw (IA falhou ou indisponível)
    missing_optional_cols: list[str] = field(default_factory=list)  # SKU, categoria, etc.
    unrecognized_columns: list[str] = field(default_factory=list)   # Colunas não mapeadas

    # Metadados do arquivo
    sheet_name: str = ""
    header_row_index: int = 0       # Índice base-0 da linha do cabeçalho
    total_sheets: int = 1

    @property
    def success_rate(self) -> float:
        """Taxa de linhas com produtos válidos sobre total de linhas de dados."""
        data_rows = self.total_rows_scanned - self.skipped_empty
        if data_rows == 0:
            return 0.0
        return round(self.valid_products / data_rows, 4)

    @property
    def skip_rate(self) -> float:
        """Taxa de linhas ignoradas."""
        if self.total_rows_scanned == 0:
            return 0.0
        total_skipped = (
            self.skipped_invalid_name
            + self.skipped_invalid_cost
            + self.skipped_subtotal
            + self.skipped_duplicate_sku
        )
        return round(total_skipped / self.total_rows_scanned, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rows_scanned": self.total_rows_scanned,
            "valid_products": self.valid_products,
            "success_rate": self.success_rate,
            "skipped": {
                "empty": self.skipped_empty,
                "invalid_name": self.skipped_invalid_name,
                "invalid_cost": self.skipped_invalid_cost,
                "subtotal": self.skipped_subtotal,
                "duplicate_sku": self.skipped_duplicate_sku,
                "header_repeat": self.skipped_header_repeat,
            },
            "cost_parse_errors": self.cost_parse_errors,
            "names_normalized": self.names_normalized,
            "names_fallback": self.names_fallback,
            "missing_optional_cols": self.missing_optional_cols,
            "unrecognized_columns": self.unrecognized_columns,
            "sheet_name": self.sheet_name,
            "header_row_index": self.header_row_index,
        }


@dataclass
class ColumnMappingResult:
    """
    Resultado do mapeamento de colunas: nome semântico → índice na planilha.

    Inclui score de confiança por coluna e os cabeçalhos originais.
    """

    # Índices das colunas mapeadas (None = não encontrada)
    product_name: int | None = None
    cost: int | None = None
    sku: int | None = None
    category: int | None = None
    supplier: int | None = None

    # Metadados
    original_headers: list[str] = field(default_factory=list)  # Cabeçalhos como vieram
    scores: dict[str, float] = field(default_factory=dict)      # Confiança por coluna (0-1)

    @property
    def has_required_columns(self) -> bool:
        """True se as colunas obrigatórias (nome e custo) foram encontradas."""
        return self.product_name is not None and self.cost is not None

    @property
    def missing_optional(self) -> list[str]:
        """Lista de colunas opcionais que não foram encontradas."""
        missing = []
        if self.sku is None:
            missing.append("sku")
        if self.category is None:
            missing.append("category")
        if self.supplier is None:
            missing.append("supplier")
        return missing

    def get_index(self, semantic_name: str) -> int | None:
        return getattr(self, semantic_name, None)


@dataclass
class ParseResult:
    """
    Resultado completo do parsing de um catálogo XLSX.

    Contém tudo que o scout_service precisa para salvar os produtos
    e que o task Celery precisa para atualizar o status do catálogo.
    """

    # Dados extraídos
    products: list[dict] = field(default_factory=list)

    # Avaliação de qualidade
    confidence: ParseConfidence = ParseConfidence.FAILED
    stats: ParseStats = field(default_factory=ParseStats)
    column_mapping: ColumnMappingResult = field(default_factory=ColumnMappingResult)

    # Diagnóstico
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def to_metadata_dict(self) -> dict[str, Any]:
        """
        Serializa para armazenar em catalog.parse_metadata (JSONB).
        Não inclui os produtos (apenas estatísticas e diagnóstico).
        """
        return {
            "confidence": self.confidence.value,
            "stats": self.stats.to_dict(),
            "column_mapping": {
                "product_name": self.column_mapping.product_name,
                "cost": self.column_mapping.cost,
                "sku": self.column_mapping.sku,
                "category": self.column_mapping.category,
                "supplier": self.column_mapping.supplier,
                "original_headers": self.column_mapping.original_headers,
                "scores": self.column_mapping.scores,
            },
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def to_metadata_json(self) -> str:
        return json.dumps(self.to_metadata_dict(), ensure_ascii=False, default=str)

    def summary(self) -> str:
        """Resumo legível para logs."""
        return (
            f"[{self.confidence.value}] "
            f"{self.stats.valid_products} produtos válidos / "
            f"{self.stats.total_rows_scanned} linhas | "
            f"taxa: {self.stats.success_rate:.1%} | "
            f"avisos: {len(self.warnings)}"
        )
