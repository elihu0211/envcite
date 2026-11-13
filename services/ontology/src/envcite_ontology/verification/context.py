"""上層條文：款、目只是半句話，主體與處罰常寫在上一層的項。

抽取者與審查者都讀得到上層，但錨點仍在單元自己的文字上：引文只能取自單元本身，上層只供理解。
"""

from envcite_ontology.db import Conn

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
