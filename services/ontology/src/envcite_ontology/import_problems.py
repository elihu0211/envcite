"""匯入問題（`source.import_problem`）：解析與寫入時發現、但不使整批失敗的資料問題。

問題掛在來源項目（`source.item`）上，隨每次匯入重算。各來源的寫入者共用這裡的型別與寫入，
各自決定是整份換掉，還是只換自己負責的代碼。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from envcite_ontology.db import Conn

type Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class Problem:
    """解析或寫入時發現的一筆資料問題，掛在來源項目上。"""

    severity: Severity
    #: 問題代碼，彙總與查詢都以它分組。
    code: str
    #: 指出是哪一處的人讀說明。
    detail: str = ""


def record(conn: Conn, item_id: object, snapshot_id: int, problems: Iterable[Problem]) -> None:
    """為來源項目新增問題；不動既有的列。"""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO source.import_problem"
            " (item_id, snapshot_id, severity, problem_code, detail)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(item_id, snapshot_id, p.severity, p.code, p.detail) for p in problems],
        )


def replace(conn: Conn, item_id: object, snapshot_id: int, problems: Iterable[Problem]) -> None:
    """來源項目的問題整份換成 `problems`。"""
    conn.execute("DELETE FROM source.import_problem WHERE item_id = %s", (item_id,))
    record(conn, item_id, snapshot_id, problems)
