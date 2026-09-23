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
from enum import Enum
from types import TracebackType
from typing import Self

from psycopg_pool import ConnectionPool

#: Shape of a local id. Mirrors the CHECK on `legal_resource.id_local`; both are
#: needed. The database one guards writes, this one avoids a round trip for a value
#: that cannot possibly match. It is not injection defence — the queries are
#: parameterised — it is a cheap reject.
_LOCAL_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


class VersionStatus(Enum):
    """Why a point-in-time resolution did or did not produce a text."""

    RESOLVED = "resolved"
    #: No such law in the corpus.
    UNKNOWN_LAW = "unknown_law"
    #: The law exists, but no held version covers the requested date.
    NO_VERSION = "no_version"


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


#: The text in force at a date, or — when `p_on` is NULL — the one with no end.
#: Resolution happens in SQL so that "which version" has exactly one answer; two
#: callers each picking a version in Python would eventually pick differently.
_RESOLVE_EXPRESSION = """
    SELECT e.iri, e.title, e.language
      FROM legal_expression AS e
     WHERE e.resource_id = %(law)s
       AND (
             (%(on)s::date IS NULL AND e.valid_to IS NULL)
          OR (%(on)s::date IS NOT NULL
              AND daterange(e.valid_from, e.valid_to, '[)') @> %(on)s::date)
           )
     LIMIT 1
"""

_GET_RESOURCE = "SELECT iri, date_document FROM legal_resource WHERE id_local = %s"

_LIST_ARTICLES = """
    SELECT s.iri, s.number
      FROM subdivision AS s
     WHERE s.expression_iri = %s
     ORDER BY s.sort_key
"""


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

    def get_law(self, law_id: str, *, as_of: str = "") -> LawRecord | None:
        """The law as in force at `as_of` (ISO date), or currently when empty.

        Returns None both when the law is unknown and when no version covers the
        date. Those are different facts, and `version_status` tells them apart —
        this method is the convenience path for callers that do not care.
        """
        status, record = self.resolve(law_id, as_of=as_of)
        return record if status is VersionStatus.RESOLVED else None

    def resolve(self, law_id: str, *, as_of: str = "") -> tuple[VersionStatus, LawRecord | None]:
        """Resolves a law at a point in time, distinguishing the two ways it fails.

        `NO_VERSION` is the one that matters: the law exists but the corpus does
        not hold the text in force on that date. Answering with the current text
        would be indistinguishable from a correct answer, so the caller has to be
        able to say it does not know.
        """
        if not is_valid_local_id(law_id):
            return VersionStatus.UNKNOWN_LAW, None
        on = as_of or None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_GET_RESOURCE, (law_id,))
            resource = cur.fetchone()
            if resource is None:
                return VersionStatus.UNKNOWN_LAW, None
            iri, date_document = resource

            cur.execute(_RESOLVE_EXPRESSION, {"law": law_id, "on": on})
            expression = cur.fetchone()
        if expression is None:
            return VersionStatus.NO_VERSION, None
        _, title, language = expression
        return VersionStatus.RESOLVED, LawRecord(
            id=law_id,
            iri=iri,
            title=title,
            language=language,
            # An unknown date is NULL in the database; the wire contract is a
            # string, so "unknown" is the empty string.
            date_document=date_document.isoformat() if date_document is not None else "",
        )

    def list_articles(self, law_id: str, *, as_of: str = "") -> list[ArticleRecord] | None:
        """Articles of the text in force at `as_of`.

        None when the law is unknown or no version covers the date; [] when that
        version genuinely has no articles. The two must stay distinguishable: one
        means the corpus lacks it, the other is a fact about the law.
        """
        if not is_valid_local_id(law_id):
            return None
        on = as_of or None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_RESOLVE_EXPRESSION, {"law": law_id, "on": on})
            expression = cur.fetchone()
            if expression is None:
                return None
            cur.execute(_LIST_ARTICLES, (expression[0],))
            return [ArticleRecord(iri=iri, number=number) for iri, number in cur.fetchall()]
