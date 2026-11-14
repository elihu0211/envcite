"""模型閘門共用：抽取者的名字，與驗證者是否有資格驗證。"""

from collections.abc import Sequence
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch_one
from envcite_ontology.verification.models import Model, family_of


class _ProducerRow(NamedTuple):
    producer: str


_SQL_PRODUCER = (
    "SELECT d.producer FROM annotation.statement AS s"
    "  JOIN annotation.assertion AS sc ON sc.id = s.id"
    "  JOIN annotation.derivation AS d ON d.id = sc.derivation_id WHERE s.id = %s"
)


def extractor_of(conn: Conn, statement_id: int) -> str:
    """陳述的抽取者；沒有這個陳述則 `LookupError`。"""
    row = fetch_one(conn, _SQL_PRODUCER, (statement_id,), _ProducerRow)
    if row is None:
        raise LookupError(f"no such statement: {statement_id}")
    return row.producer


def eligible(
    models: Sequence[Model], extractor: str, allow_same_family: bool
) -> tuple[list[Model], list[str]]:
    """合格的驗證者：非抽取者，預設也非同家族。回傳（合格者，被略過者的名字）。"""
    home = family_of(extractor)
    ok = [m for m in models if m.name != extractor and (allow_same_family or m.family != home)]
    return ok, [m.name for m in models if m not in ok]
