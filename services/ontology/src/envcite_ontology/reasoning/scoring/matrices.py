"""從資料庫載入裁罰矩陣規格。"""

from datetime import date
from decimal import Decimal
from typing import Literal, NamedTuple

from envcite_ontology.db import Conn, fetch
from envcite_ontology.reasoning.scoring.models import (
    FactorRole,
    FactorSpec,
    FormulaKind,
    PenaltyMatrixSpec,
)


class _MatrixRow(NamedTuple):
    id: int
    matrix_code: str
    law_title: str
    norm_kind: Literal["delegated_regulation", "discretion_standard"]
    formula_kind: FormulaKind
    legal_effect: str
    base_amount: Decimal | None
    statutory_min: Decimal | None
    statutory_max: Decimal | None
    demerit_threshold: Decimal | None
    rounding_mode: Literal["floor_ntd", "round_ntd", "ceil_ntd", "none"]
    provision_id: int
    statutory_basis: str


class _FactorRow(NamedTuple):
    matrix_id: int
    factor_code: str
    factor_role: FactorRole
    label_zho: str
    statutory_ref: str
    default_value: Decimal | None
    min_value: Decimal | None
    max_value: Decimal | None
    needs_judgement: bool
    description: str


def load_matrices(conn: Conn, as_of: date, language: str = "zho") -> dict[str, PenaltyMatrixSpec]:
    """自 PostgreSQL `scoring.penalty_matrix` 載入 `as_of` 當日有效條文所錨定之裁罰矩陣。"""
    rows = fetch(
        conn,
        """
        SELECT m.id, m.matrix_code, m.law_title, m.norm_kind, m.formula_kind::text,
               m.legal_effect::text, m.base_amount, m.statutory_min, m.statutory_max,
               m.demerit_threshold, m.rounding_mode, m.provision_id,
               coalesce(m.metadata->>'statutory_basis', '')
          FROM scoring.penalty_matrix AS m
          JOIN corpus.provision AS p ON p.id = m.provision_id
          JOIN corpus.provision_version AS v
            ON v.provision_id = m.provision_id AND v.content_sha256 = m.content_sha256
           AND v.superseded_at IS NULL AND v.force_period @> %s
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id AND e.language = %s
         ORDER BY m.matrix_code
        """,
        (as_of, language),
        _MatrixRow,
    )
    if not rows:
        return {}
    matrix_ids = [r.id for r in rows]
    factor_rows = fetch(
        conn,
        """
        SELECT matrix_id, factor_code, factor_role::text, label_zho, statutory_ref,
               default_value, min_value, max_value, needs_judgement, description
          FROM scoring.matrix_factor
         WHERE matrix_id = ANY(%s)
         ORDER BY matrix_id, id
        """,
        (matrix_ids,),
        _FactorRow,
    )
    by_matrix: dict[int, list[FactorSpec]] = {}
    for fr in factor_rows:
        by_matrix.setdefault(fr.matrix_id, []).append(
            FactorSpec(
                factor_code=fr.factor_code,
                factor_role=fr.factor_role,
                label_zho=fr.label_zho,
                statutory_ref=fr.statutory_ref,
                default_value=fr.default_value,
                min_value=fr.min_value,
                max_value=fr.max_value,
                needs_judgement=fr.needs_judgement,
                description=fr.description,
            )
        )
    return {
        r.matrix_code: PenaltyMatrixSpec(
            matrix_code=r.matrix_code,
            law_title=r.law_title,
            norm_kind=r.norm_kind,
            formula_kind=r.formula_kind,
            legal_effect=r.legal_effect,
            base_amount=r.base_amount,
            statutory_min=r.statutory_min,
            statutory_max=r.statutory_max,
            demerit_threshold=r.demerit_threshold,
            rounding_mode=r.rounding_mode,
            factors=tuple(by_matrix.get(r.id, ())),
            provision_id=r.provision_id,
            statutory_basis=r.statutory_basis,
        )
        for r in rows
    }
