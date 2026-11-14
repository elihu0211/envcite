"""各版本條文的文字：正規化比對、沿用較新一版的文字，與現行頁的核對。"""

import re
import unicodedata
from collections import Counter

from envcite_ontology.ingest.backfill.models import Version


def fold(text: str) -> str:
    """比對用：相容字形與空白不算差異（各系統的括號全半形、英數前後的空白不同）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def reuse_texts(
    version: Version, reference: dict[str, str], newer: Version | None
) -> tuple[dict[str, str], int]:
    """各條的文字：與較新一版相同（正規化後，或與來源的較新一版相同）的沿用較新一版的文字。"""
    known = {fold(t): t for t in reference.values()}
    if newer is not None:
        known |= {
            fold(a.content): reference[a.article_no]
            for a in newer.articles
            if a.article_no in reference and fold(a.content) not in known
        }
    texts: dict[str, str] = {}
    ambiguous = 0
    for a in version.articles:
        same = known.get(fold(a.content))
        texts[a.article_no] = same if same is not None else a.content
        ambiguous += a.ambiguous if same is None else 0
    return texts, ambiguous


def check_current(current: Version, latest: dict[str, str]) -> Counter[str]:
    """現行頁與全國法規資料庫現行文字的核對。

    各條計入 exact、typography、structure、differs、incomplete、missing 之一。
    """
    got = {a.article_no: a for a in current.articles}
    out: Counter[str] = Counter()
    for no, text in latest.items():
        a = got.get(no)
        if a is None:
            out["missing"] += 1
        elif a.incomplete:
            out["incomplete"] += 1
        elif a.content == text:
            out["exact"] += 1
        elif fold(a.content) != fold(text):
            out["differs"] += 1
        elif a.content.count("\n") == text.count("\n"):
            out["typography"] += 1
        else:
            out["structure"] += 1
    return out
