"""匯入問題的記錄：計入報告，並記在來源項目的最後快照上。"""

from collections.abc import Sequence
from typing import NamedTuple

from envcite_ontology import import_problems
from envcite_ontology.db import Conn
from envcite_ontology.guidance_sync.report import Report
from envcite_ontology.import_problems import Problem


class ItemRow(NamedTuple):
    """來源項目與它最後一次出現的快照。"""

    id: int
    last_snapshot_id: int


REPEAL_PROBLEMS = ("repeal_unmatched", "repeal_ambiguous", "repeal_before_issue", "repeal_rejected")


def record_problems(
    conn: Conn, report: Report, item_id: int, snapshot_id: int, problems: Sequence[Problem]
) -> None:
    """匯入問題計入報告，並記在來源項目於該快照的紀錄上。"""
    report.problems.update(p.code for p in problems)
    import_problems.record(conn, item_id, snapshot_id, problems)
