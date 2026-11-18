"""上層條文：款、目只是半句話，主體與處罰常寫在上一層的項。

抽取者與審查者都讀得到上層，但錨點仍在單元自己的文字上：引文只能取自單元本身，上層只供理解。

既有概念：抽取者宣告概念前先看到方案裡已有的，同一個意思才不會再得到另一個代碼與名稱。
"""

import re
from typing import NamedTuple

from canon.db import Conn, fetch

_UP = """
WITH RECURSIVE up(id, parent_id, depth) AS (
    SELECT p.id, p.parent_id, 0 FROM corpus.provision AS p WHERE p.id = %s
  UNION ALL
    SELECT p.id, p.parent_id, up.depth + 1
      FROM corpus.provision AS p JOIN up ON p.id = up.parent_id
)
SELECT DISTINCT ON (up.depth) v.content
  FROM up JOIN corpus.provision_version AS v
    ON v.provision_id = up.id AND v.superseded_at IS NULL
 WHERE up.depth > 0 AND v.content IS NOT NULL AND btrim(v.content) <> ''
 ORDER BY up.depth DESC, v.id DESC
"""


class _ContentRow(NamedTuple):
    content: str


def ancestors_text(conn: Conn, provision_id: int) -> str:
    """單元所有上層的現行文字，由外到內，換行相接；頂層單元（或上層沒有文字）是空字串。"""
    return "\n".join(row.content for row in fetch(conn, _UP, (provision_id,), _ContentRow))


class KnownConcept(NamedTuple):
    """方案裡已有的概念：代碼與名稱。"""

    code: str
    name: str


_CONCEPTS = (
    "SELECT k.code, k.name FROM vocab.concept AS k"
    "  JOIN vocab.concept_scheme AS s ON s.id = k.scheme_id"
    " WHERE s.code = %s ORDER BY k.id"
)

#: 概念名稱至少有這個比例的字雙連在單元文字裡出現，才算與這個單元相關。
NAME_COVERAGE = 0.6
#: 給抽取者看的既有概念上限；提示不因詞彙變大而變長。
KNOWN_LIMIT = 40


def _grams(text: str) -> set[str]:
    """文字的字雙連（只留文字與數字，標點與空白不算）；單字的名稱就是它自己。"""
    kept = re.sub(r"[^\w]", "", text)
    return {kept[i : i + 2] for i in range(len(kept) - 1)} or ({kept} if kept else set())


def known_concepts(
    conn: Conn, scheme: str, text: str, *, limit: int = KNOWN_LIMIT
) -> list[KnownConcept]:
    """方案裡與這段文字相關的既有概念：名稱的字雙連多半在文字裡，相關的排前面，最多 `limit` 個。

    用字面而不用嵌入：條文常把概念名稱的字照樣寫出來，不依賴嵌入端點，同一段文字永遠得到同一份清單。
    """
    have = _grams(text)
    scored: list[tuple[float, int, KnownConcept]] = []
    for order, row in enumerate(fetch(conn, _CONCEPTS, (scheme,), KnownConcept)):
        want = _grams(row.name)
        covered = len(want & have) / len(want) if want else 0.0
        if covered >= NAME_COVERAGE:
            scored.append((covered, order, row))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [row for _, _, row in scored[:limit]]
