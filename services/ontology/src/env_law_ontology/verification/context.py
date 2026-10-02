"""上層條文：款、目只是半句話，主體與處罰的種類、額度常寫在上一層的項裡。

匯入把條文拆成條、項、款、目，每一層是一個單元，陳述錨在它自己的文字上。可是「有下列情形之一者，
處新臺幣…罰鍰：一、違反…」這樣的條文，罰鍰寫在項，違反的行為寫在款；只看款的人看不到處罰，
只看項的人看不到違反了什麼。抽取者與審查者都要看得到上層，但**錨點仍在單元自己的文字上**：
引文只能取自單元本身，上層只供理解（與判斷罰則的效果與額度是否有依據）。
"""

from __future__ import annotations

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]

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


def ancestors_text(conn: Conn, provision_id: int) -> str:
    """單元所有上層的現行文字，由外到內，換行相接；頂層單元（或上層沒有文字）是空字串。"""
    return "\n".join(str(row[0]) for row in conn.execute(_UP, (provision_id,)))


def context_block(context: str) -> str:
    """放進提示的上層條文區塊；沒有上層就什麼都不放。"""
    if not context:
        return ""
    return (
        f"上層條文（只供理解，不是要標註的單元；引文仍須逐字取自單元自己的原文）：\n{context}\n\n"
    )
