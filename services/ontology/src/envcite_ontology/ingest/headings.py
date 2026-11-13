"""從來源法規條文陣列抽取條之上的結構章節（編、章、節、款、目）。

全國法規資料庫的 `LawArticles` 以 `ArticleType == "C"` 依閱讀順序列出編章節標題，
緊接在其後的 `ArticleType == "A"` 即為該章節管轄的第一條。本模組把這些標題轉成
帶有 `parent_index` 與交錯 `sort_key` 的平直清單，讓資料庫可用單一 `ORDER BY`
把章節與條文按文件順序組樹。
"""

import re
from dataclasses import dataclass
from typing import Any

from envcite_ontology.ingest import articles

_KIND_BY_CHAR: dict[str, str] = {
    "編": "part",
    "章": "chapter",
    "節": "section",
    "款": "subsection",
    "目": "item_heading",
}

_RANK: dict[str, int] = {
    "part": 1,
    "chapter": 2,
    "section": 3,
    "subsection": 4,
    "item_heading": 5,
}

_HEADING_RE = re.compile(
    r"^\s*(第\s*[零一二三四五六七八九十百千0-9]+\s*(?:之\s*[零一二三四五六七八九十百千0-9]+\s*)?([編章節款目]))\s*(.*)$"
)

_TRAILING_SORT_BASE = 999_999_999
_HEADING_PAD = 999_999


@dataclass(frozen=True)
class ParsedHeading:
    """解析出的一個結構章節標題。"""

    kind: str
    heading_no: str
    title: str
    sort_key: list[int]
    parent_index: int | None


def parse_heading_text(raw: str) -> tuple[str, str, str] | None:
    """把 `ArticleContent` 拆成 `(kind, heading_no, title)`；空白字串回 `None`。"""
    text = raw.strip()
    if not text:
        return None
    match = _HEADING_RE.match(text)
    if match is None:
        return ("chapter", text, "")
    raw_no, unit_char, rest = match.groups()
    heading_no = re.sub(r"\s+", "", raw_no)
    return (_KIND_BY_CHAR[unit_char], heading_no, rest.strip())


def sort_key_before(next_article_key: list[int] | None, seq: int) -> list[int]:
    """計算排在 `next_article_key` 之前、同位置第 `seq` 個章節的 `sort_key`。"""
    if not next_article_key:
        return [_TRAILING_SORT_BASE, seq]
    return [*next_article_key[:-1], next_article_key[-1] - 1, _HEADING_PAD, seq]


def extract(raw_articles: list[dict[str, Any]]) -> list[ParsedHeading]:
    """依 `LawArticles` 出現順序抽取結構章節，建立父子索引與交錯 `sort_key`。"""
    headings: list[ParsedHeading] = []
    pending: list[tuple[str, str, str]] = []
    stack: list[tuple[int, int]] = []

    def _flush(next_key: list[int] | None) -> None:
        for seq, (kind, heading_no, title) in enumerate(pending):
            rank = _RANK[kind]
            while stack and stack[-1][0] >= rank:
                stack.pop()
            parent_index = stack[-1][1] if stack else None
            idx = len(headings)
            headings.append(
                ParsedHeading(
                    kind=kind,
                    heading_no=heading_no,
                    title=title,
                    sort_key=sort_key_before(next_key, seq),
                    parent_index=parent_index,
                )
            )
            stack.append((rank, idx))
        pending.clear()

    for entry in raw_articles:
        kind_flag = entry.get("ArticleType")
        if kind_flag == "C":
            parsed = parse_heading_text(str(entry.get("ArticleContent") or ""))
            if parsed is not None:
                pending.append(parsed)
        elif kind_flag == "A" and pending:
            content = str(entry.get("ArticleContent", ""))
            if articles.is_deleted(content):
                continue
            path = articles.article_path(str(entry.get("ArticleNo", "")))
            if path is not None:
                _flush(articles.sort_key(path))

    if pending:
        _flush(None)
    return headings
