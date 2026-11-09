"""範圍工具：題庫引用的法規，由真實資料庫的標題與條文代號解出。"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import psycopg

from tests.sample import AIR, WATER

Conn = psycopg.Connection[tuple[object, ...]]

_path = Path(__file__).resolve().parents[1] / "tools" / "golden_scope.py"
_spec = importlib.util.spec_from_file_location("golden_scope_module", _path)
assert _spec is not None and _spec.loader is not None
_tool = importlib.util.module_from_spec(_spec)
sys.modules["golden_scope_module"] = _tool
_spec.loader.exec_module(_tool)


def test_cited_laws_come_from_titles_and_article_ids(conn: Conn, sample: None) -> None:
    items: list[dict[str, Any]] = [
        {
            "id": "by-title",
            "bucket": "exact_id",
            "citation_allowlist": [{"statute": "空氣污染防制法"}],
        },
        {
            "id": "by-id",
            "bucket": "exact_id",
            "citation_allowlist": [{"article_id": f"{WATER}-art-7"}, {"statute": "不存在法"}],
        },
    ]
    scope = _tool.build(items, conn)

    assert scope["counts"]["cited"] == 2
    assert [e["pcode"] for e in scope["laws"]] == [AIR, WATER]
    assert {e["part"] for e in scope["laws"]} == {"cited"}
