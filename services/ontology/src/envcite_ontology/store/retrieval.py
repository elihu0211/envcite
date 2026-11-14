"""混合檢索：字面臂與語意臂的候選、函釋入口、問句點名的法規，與斷詞用的詞典。"""

from collections.abc import Sequence

from envcite_ontology.embedding import to_sql
from envcite_ontology.law_names import LawNames
from envcite_ontology.store.params import as_of_param, expiry, time_left
from envcite_ontology.store.ports import ConnectionSource
from envcite_ontology.store.records import (
    LANGUAGE,
    SearchHit,
)

#: segmenter 詞典（法規名稱）；須與建索引同源，否則分詞器識別碼不符、字面檢索全落空（04-01 §5）。
_LEXICON = "SELECT DISTINCT title FROM corpus.legal_expression WHERE title IS NOT NULL"

#: 標題（含各版本的舊名稱）→ 法規代碼；問句點名法規用（`law_names.LawNames`）。
_LAW_TITLES = """
    SELECT e.title, array_agg(DISTINCT r.local_id)
      FROM corpus.legal_expression AS e
      JOIN corpus.legal_resource AS r ON r.id = e.resource_id
     WHERE e.title IS NOT NULL
     GROUP BY e.title
"""

_SEARCH = """
    SELECT corpus.version_iri(s.version_id), r.jurisdiction, r.id_scheme, r.local_id, e.title,
           corpus.article_number(s.path), corpus.article_text(s.version_id),
           s.lexical_score, s.semantic_similarity
      FROM retrieval.search_expanded(
               retrieval.prune_terms(%(terms)s, %(tokenizer)s, %(max_df)s::double precision),
               %(tokenizer)s, %(embedding)s::vector, %(model)s,
               %(on)s::date, %(language)s, %(limit)s, %(pool)s,
               CASE WHEN cardinality(%(laws)s::text[]) = 0 THEN NULL
                    ELSE retrieval.law_scope(%(laws)s::text[], %(language)s, %(on)s::date)
               END
           ) AS s
      JOIN corpus.legal_resource AS r ON r.id = s.resource_id
      JOIN corpus.provision_version AS v ON v.id = s.version_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     WHERE (%(jurisdiction)s::corpus.jurisdiction IS NULL
            OR r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction)
     ORDER BY s.rank
"""

_GUIDANCE = """
    SELECT corpus.version_iri(g.version_id), r.jurisdiction, r.id_scheme, r.local_id, e.title,
           corpus.article_number(g.path), corpus.article_text(g.version_id), g.documents
      FROM retrieval.guidance_articles(
               %(phrases)s::text[], %(on)s::date, %(language)s, %(limit)s,
               %(max_df)s::double precision) AS g
      JOIN corpus.legal_resource AS r ON r.id = g.resource_id
      JOIN corpus.provision_version AS v ON v.id = g.version_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     WHERE (%(jurisdiction)s::corpus.jurisdiction IS NULL
            OR r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction)
     ORDER BY g.documents DESC, g.version_id
"""

#: 每一臂的候選池（條數）。融合前各取這麼多；取 `limit` 上限的數倍。
#: 改它要用 `tools/golden_retrieval_eval.py` 在開發集量過。
SEARCH_POOL = 50


class RetrievalRepository:
    """檢索的唯讀查詢；連線由外面給（見 `ports.ConnectionSource`）。"""

    def __init__(self, connections: ConnectionSource) -> None:
        self._connections = connections

    def lexicon(self) -> list[str]:
        """segmenter 的詞典：語料所有版本的法規標題（不重複）。"""
        with self._connections.connection() as conn, conn.cursor() as cur:
            cur.execute(_LEXICON)
            return [title for (title,) in cur.fetchall()]

    def law_names(self) -> LawNames:
        """法規標題（含各版本的舊名稱）對法規代碼的對照，供問句點名法規用。"""
        with self._connections.connection() as conn, conn.cursor() as cur:
            cur.execute(_LAW_TITLES)
            return LawNames({title: pcodes for title, pcodes in cur.fetchall()})

    def guidance_hits(
        self,
        phrases: Sequence[str],
        *,
        as_of: str = "",
        limit: int = 10,
        jurisdiction: str | None = None,
        max_df: float = 0.0,
        timeout: float | None = None,
    ) -> list[tuple[SearchHit, int]]:
        """函釋（主旨或內文含每個詞組、`as_of` 有效）所解釋的條文，連同提到它的函釋件數；多的在前。

        函釋只用來找條文，不是依據（`retrieval.guidance_articles`）。命中沒有字面分數與語意相似度。
        命中的函釋占可用函釋的比例超過 `max_df` 時不回任何條：詞組出現在那麼多函釋裡，指不出哪幾條
        （0 為不限制）。
        """
        with self._connections.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _GUIDANCE,
                {
                    "phrases": list(phrases),
                    "on": as_of_param(as_of),
                    "language": LANGUAGE,
                    "limit": limit,
                    "jurisdiction": jurisdiction,
                    "max_df": max_df,
                },
            )
            return [
                (
                    SearchHit(
                        iri=iri,
                        jurisdiction=jur,
                        id_scheme=scheme,
                        local_id=lid,
                        law_title=title,
                        number=number,
                        content=content,
                        lexical_score=0.0,
                        semantic_similarity=None,
                    ),
                    int(documents),
                )
                for iri, jur, scheme, lid, title, number, content, documents in cur.fetchall()
            ]

    def searched_terms(
        self, terms: str, *, tokenizer: str, max_df: float = 0.0, timeout: float | None = None
    ) -> list[str]:
        """字面臂實際查的詞：`search` 以同樣的 `max_df` 略過高頻詞之後留下的詞。"""
        with self._connections.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT retrieval.prune_terms("
                "%(terms)s, %(tokenizer)s, %(max_df)s::double precision)",
                {"terms": terms, "tokenizer": tokenizer, "max_df": max_df},
            )
            row = cur.fetchone()
        return str(row[0] if row else "").split()

    def search(
        self,
        terms: str,
        *,
        tokenizer: str,
        as_of: str = "",
        limit: int = 10,
        embedding: Sequence[float] | None = None,
        model: str = "",
        max_df: float = 0.0,
        laws: Sequence[str] = (),
        jurisdiction: str | None = None,
        timeout: float | None = None,
    ) -> list[SearchHit]:
        """`as_of`（空為今日）有效的條，字面臂比對已分詞的 `terms`，語意臂比對 `embedding`。

        沒有 `embedding` 時只用字面臂。兩臂以名次倒數融合排序（`retrieval.search_hybrid`），每條之後
        緊接處罰它（或授權其所在命令的條）的罰則條，分數取帶出它的那一條（`retrieval.search_expanded`）。
        `max_df`（段落比例）大於 0 時，字面臂先略過出現在超過這個比例的段落的詞
        （`retrieval.prune_terms`）；0 為不修剪。

        `laws`（法規代碼）非空時兩臂只在這些法規與依其授權訂定的命令裡找
        （`retrieval.law_scope`）；範圍在 `as_of` 沒有任何可檢索的條時，改在全庫找，不回空。

        `timeout`（秒）是這一次檢索剩下的預算，兩次查詢共用；沒有期限時為 None。
        """
        deadline = expiry(timeout)
        hits = self._search(
            terms,
            tokenizer,
            as_of,
            limit,
            embedding,
            model,
            max_df,
            tuple(laws),
            jurisdiction,
            timeout=time_left(deadline),
        )
        if laws and not hits:
            hits = self._search(
                terms,
                tokenizer,
                as_of,
                limit,
                embedding,
                model,
                max_df,
                (),
                jurisdiction,
                timeout=time_left(deadline),
            )
        return hits

    def _search(
        self,
        terms: str,
        tokenizer: str,
        as_of: str,
        limit: int,
        embedding: Sequence[float] | None,
        model: str,
        max_df: float,
        laws: tuple[str, ...],
        jurisdiction: str | None = None,
        timeout: float | None = None,
    ) -> list[SearchHit]:
        with self._connections.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _SEARCH,
                {
                    "laws": list(laws),
                    "terms": terms,
                    "tokenizer": tokenizer,
                    "max_df": max_df,
                    "on": as_of_param(as_of),
                    "language": LANGUAGE,
                    "limit": limit,
                    "embedding": None if embedding is None else to_sql(embedding),
                    "model": model,
                    "pool": SEARCH_POOL,
                    "jurisdiction": jurisdiction,
                },
            )
            return [
                SearchHit(
                    iri=iri,
                    jurisdiction=jur,
                    id_scheme=scheme,
                    local_id=lid,
                    law_title=title,
                    number=number,
                    content=content,
                    lexical_score=lexical,
                    semantic_similarity=semantic,
                )
                for (
                    iri,
                    jur,
                    scheme,
                    lid,
                    title,
                    number,
                    content,
                    lexical,
                    semantic,
                ) in cur.fetchall()
            ]
