"""Read model over the ELI law graph (pyoxigraph, in-memory)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from pyoxigraph import Literal, RdfFormat, Store

ELI = "http://data.europa.eu/eli/ontology#"
LAW_BASE = "https://env.law/eli/tw/law/"

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


def _article_sort_key(number: str) -> tuple[int, ...]:
    # "5-1" (第5條之1) sorts after "5" and before "6".
    return tuple(int(part) for part in number.split("-"))


class OntologyStore:
    def __init__(self, store: Store) -> None:
        self._store = store

    @classmethod
    def from_path(cls, path: Path) -> OntologyStore:
        store = Store()
        store.load(path=str(path), format=RdfFormat.TURTLE)
        return cls(store)

    @classmethod
    def from_sample(cls) -> OntologyStore:
        return cls.from_path(Path(str(files("env_law.data").joinpath("laws.ttl"))))

    def get_law(self, law_id: str) -> LawRecord | None:
        # law_id is validated before being embedded in the query (no SPARQL injection).
        if not is_valid_local_id(law_id):
            return None
        query = f"""
            PREFIX eli: <{ELI}>
            SELECT ?law ?title ?date WHERE {{
                ?law a eli:LegalResource ; eli:id_local "{law_id}" ; eli:is_realized_by ?expr .
                ?expr eli:title ?title .
                OPTIONAL {{ ?law eli:date_document ?date }}
            }} LIMIT 1
        """
        for solution in self._store.query(query):  # type: ignore[union-attr]
            title = solution["title"]
            date = solution["date"]
            return LawRecord(
                id=law_id,
                iri=solution["law"].value,
                title=title.value,
                language=title.language or "" if isinstance(title, Literal) else "",
                date_document=date.value if date is not None else "",
            )
        return None

    def list_articles(self, law_id: str) -> list[ArticleRecord] | None:
        law = self.get_law(law_id)
        if law is None:
            return None
        query = f"""
            PREFIX eli: <{ELI}>
            SELECT ?article ?number WHERE {{
                ?article a eli:LegalResourceSubdivision ;
                    eli:is_part_of <{law.iri}> ; eli:number ?number .
            }}
        """
        articles = [
            ArticleRecord(iri=s["article"].value, number=s["number"].value)
            for s in self._store.query(query)  # type: ignore[union-attr]
        ]
        return sorted(articles, key=lambda a: _article_sort_key(a.number))
