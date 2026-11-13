"""（法規代碼，條文路徑）→ 條文單元。

標註、裁判與行政規則都要這一步。查詢只認全國法規資料庫的代碼，不猜其他方案。
"""

from typing import NamedTuple

from envcite_ontology.db import Conn, fetch_one


class _ProvisionRow(NamedTuple):
    id: int


class UnknownProvision(LookupError):
    """語料裡沒有這個單元。"""


def provision_id(conn: Conn, law: object, path: object) -> int:
    """`moj_pcode` 與路徑對到的條文單元。沒有則丟 `UnknownProvision`。"""
    row = fetch_one(
        conn,
        "SELECT p.id FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s",
        (law, path),
        _ProvisionRow,
    )
    if row is None:
        raise UnknownProvision(f"語料裡沒有 {law} 的單元 {path}")
    return row.id
