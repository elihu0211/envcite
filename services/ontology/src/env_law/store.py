"""Read model over the ELI law data (PostgreSQL).

The relational tables are the truth; the ontology is a view projected from them.
That direction must not be reversed — see env-law-docs 02-02. This module reads
only; writing belongs to the import pipeline, which does not exist yet.

Ordering is not done in Python. Reading order ("5-1" after "5", before "6") is
carried by the derived column `subdivision.sort_key` and the query just ORDERs by
it. Ordering is a property of the data, not a preference of the caller: leave it
to the caller and a second caller will eventually order it differently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from psycopg_pool import ConnectionPool

#: Shape of a local id. Mirrors the CHECK on `legal_resource.id_local`; both are
#: needed. The database one guards writes, this one avoids a round trip for a value
#: that cannot possibly match. It is not injection defence — the queries are
#: parameterised — it is a cheap reject.
_LOCAL_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class LawRecord:
    id: str
    iri: str
    title: str
    language: str
    date_document: str


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    iri: str
    number: str


def is_valid_local_id(value: str) -> bool:
    return bool(_LOCAL_ID.fullmatch(value))


_GET_LAW = """
    SELECT r.iri, e.title, e.language, r.date_document
      FROM legal_resource AS r
      JOIN legal_expression AS e ON e.resource_id = r.id_local
     WHERE r.id_local = %s
     LIMIT 1
"""

_LIST_ARTICLES = """
    SELECT s.iri, s.number
      FROM subdivision AS s
     WHERE s.resource_id = %s
     ORDER BY s.sort_key
"""

_LAW_EXISTS = "SELECT 1 FROM legal_resource WHERE id_local = %s"


class OntologyStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_dsn(cls, dsn: str, *, min_size: int = 1, max_size: int = 8) -> Self:
        return cls(ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True))

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def get_law(self, law_id: str) -> LawRecord | None:
        if not is_valid_local_id(law_id):
            return None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_GET_LAW, (law_id,))
            row = cur.fetchone()
        if row is None:
            return None
        iri, title, language, date_document = row
        return LawRecord(
            id=law_id,
            iri=iri,
            title=title,
            language=language,
            # An unknown date is NULL in the database; the wire contract is a string,
            # so "unknown" is the empty string.
            date_document=date_document.isoformat() if date_document is not None else "",
        )

    def list_articles(self, law_id: str) -> list[ArticleRecord] | None:
        """Articles of a law: None when the law does not exist, [] when it has none.

        The two must stay distinguishable. None maps to NOT_FOUND; an empty list means
        "this law currently has no articles", which is normal while the corpus is
        incomplete.
        """
        if not is_valid_local_id(law_id):
            return None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_LAW_EXISTS, (law_id,))
            if cur.fetchone() is None:
                return None
            cur.execute(_LIST_ARTICLES, (law_id,))
            return [ArticleRecord(iri=iri, number=number) for iri, number in cur.fetchall()]
