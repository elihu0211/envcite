"""資料庫連線的型別與型別化的查詢：各模組標註 `Conn`，欄位型別在一處宣告，不在用處轉型。

`Conn` 的列是 `tuple[object, ...]`：每一欄都要在用處轉型才能用。查詢改走 `fetch` 等函式，
每一列由 `row`（具名型別的建構子，如 `NamedTuple` 或 dataclass）依欄位次序建出，
欄位的型別與可否為 NULL 只在該型別宣告一處，用處不再 `cast`。
"""

from collections.abc import Callable

import psycopg
from psycopg.abc import Params
from psycopg.rows import args_row

type Conn = psycopg.Connection[tuple[object, ...]]


def fetch[Row](conn: Conn, sql: str, params: Params | None, row: Callable[..., Row]) -> list[Row]:
    """執行查詢，全部的列依次序交給 `row` 建出。"""
    with conn.cursor(row_factory=args_row(row)) as cur:
        return cur.execute(sql, params).fetchall()


def fetch_one[Row](
    conn: Conn, sql: str, params: Params | None, row: Callable[..., Row]
) -> Row | None:
    """執行查詢，回傳第一列建出的 `row`；沒有列為 None。"""
    with conn.cursor(row_factory=args_row(row)) as cur:
        return cur.execute(sql, params).fetchone()


def scalar[T](conn: Conn, sql: str, params: Params | None, kind: type[T]) -> T:
    """恰一列一欄的結果，並確認它是 `kind`；沒有列或型別不符丟 `LookupError`。"""
    found = conn.execute(sql, params).fetchone()
    if found is None or not isinstance(found[0], kind):
        raise LookupError(f"expected one {kind.__name__}, got {found!r}")
    return found[0]
