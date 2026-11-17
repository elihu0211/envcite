"""型別化的列在讀取邊界檢查非空宣告：資料庫給了 NULL 就當場報錯，不流進用處。"""

from dataclasses import dataclass
from typing import Any, NamedTuple

import pytest

from canon.db import Conn, NullInNonNullField, fetch, fetch_one


class _Named(NamedTuple):
    name: str
    note: str | None


@dataclass(frozen=True)
class _Counted:
    name: str
    total: int | None = None


class _Loose(NamedTuple):
    anything: Any
    whatever: object


def test_a_null_in_a_field_declared_non_null_is_refused_at_the_boundary(conn: Conn) -> None:
    with pytest.raises(NullInNonNullField, match=r"_Named\.name"):
        fetch(conn, "SELECT NULL::text, 'x'", None, _Named)


def test_a_null_in_a_nullable_field_is_kept(conn: Conn) -> None:
    assert fetch(conn, "SELECT 'a', NULL::text", None, _Named) == [_Named("a", None)]


def test_dataclass_rows_are_checked_too(conn: Conn) -> None:
    with pytest.raises(NullInNonNullField, match=r"_Counted\.name"):
        fetch_one(conn, "SELECT NULL::text", None, _Counted)
    assert fetch_one(conn, "SELECT 'a'", None, _Counted) == _Counted("a")


def test_fields_without_a_declared_type_accept_anything(conn: Conn) -> None:
    assert fetch(conn, "SELECT NULL, NULL", None, _Loose) == [_Loose(None, None)]
