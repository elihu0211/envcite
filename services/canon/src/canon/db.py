"""資料庫連線的型別與型別化的查詢：各模組標註 `Conn`，欄位型別在一處宣告，不在用處轉型。

`Conn` 的列是 `tuple[object, ...]`：每一欄都要在用處轉型才能用。查詢改走 `fetch` 等函式，
每一列由 `row`（具名型別的建構子，如 `NamedTuple` 或 dataclass）依欄位次序建出，
欄位的型別與可否為 NULL 只在該型別宣告一處，用處不再 `cast`。

宣告是契約，型別檢查看不到資料庫：欄位標了非空而資料庫給了 NULL，值會一路流進去、在用處才以
無關的錯誤崩潰（或被 `str()` 悄悄變成 `"None"`）。所以在讀取的邊界檢查：違反宣告就當場報出
是哪個型別的哪個欄位，讓來源（查詢、視圖、綱要）去修，不在消費端略過。
"""

import dataclasses
import types
import typing
from collections.abc import Callable
from functools import cache

import psycopg
from psycopg.abc import Params
from psycopg.rows import args_row

type Conn = psycopg.Connection[tuple[object, ...]]


class NullInNonNullField(LookupError):
    """列型別把這個欄位宣告為非空，資料庫卻給了 NULL：契約在來源被違反。"""


def _nullable(hint: object) -> bool:
    """欄位型別容許 None：`X | None`、`Optional[X]`，或不設限的 `Any`／`object`。"""
    if hint in (typing.Any, object, type(None)):
        return True
    if isinstance(hint, typing.TypeAliasType):
        return _nullable(hint.__value__)
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        return any(_nullable(arg) for arg in typing.get_args(hint))
    return False


@cache
def _required(row: Callable[..., object]) -> tuple[tuple[int, str], ...]:
    """列型別宣告為非空的欄位（位置與名稱）；不是具名欄位的型別（如 `tuple`）沒有可檢查的宣告。"""
    if dataclasses.is_dataclass(row):
        names = [f.name for f in dataclasses.fields(row) if f.init]
    elif hasattr(row, "_fields"):
        names = list(row._fields)
    else:
        return ()
    hints = typing.get_type_hints(row)
    return tuple((i, name) for i, name in enumerate(names) if not _nullable(hints[name]))


def _checked[Row](row: Callable[..., Row]) -> Callable[..., Row]:
    """包一層：建列前先確認宣告為非空的欄位都有值。"""
    required = _required(row)
    if not required:
        return row

    def build(*values: object) -> Row:
        for index, name in required:
            if index < len(values) and values[index] is None:
                raise NullInNonNullField(
                    f"{getattr(row, '__qualname__', row)}.{name} 宣告為非空，資料庫給了 NULL"
                )
        return row(*values)

    return build


def fetch[Row](conn: Conn, sql: str, params: Params | None, row: Callable[..., Row]) -> list[Row]:
    """執行查詢，全部的列依次序交給 `row` 建出。"""
    with conn.cursor(row_factory=args_row(_checked(row))) as cur:
        return cur.execute(sql, params).fetchall()


def fetch_one[Row](
    conn: Conn, sql: str, params: Params | None, row: Callable[..., Row]
) -> Row | None:
    """執行查詢，回傳第一列建出的 `row`；沒有列為 None。"""
    with conn.cursor(row_factory=args_row(_checked(row))) as cur:
        return cur.execute(sql, params).fetchone()


def scalar[T](conn: Conn, sql: str, params: Params | None, kind: type[T]) -> T:
    """恰一列一欄的結果，並確認它是 `kind`；沒有列或型別不符丟 `LookupError`。"""
    found = conn.execute(sql, params).fetchone()
    if found is None or not isinstance(found[0], kind):
        raise LookupError(f"expected one {kind.__name__}, got {found!r}")
    return found[0]
