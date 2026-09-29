import pytest

from env_law.store import OntologyStore


def test_get_law_reads_title_from_expression(store: OntologyStore) -> None:
    law = store.get_law("O0020001")

    assert law is not None
    assert law.title == "空氣污染防制法"
    assert law.language == "zho"
    assert law.date_document == "1975-05-23"
    assert law.iri == "https://env.law/eli/tw/law/O0020001"


@pytest.mark.parametrize("law_id", ["Z9999999", "", "o0020001", 'x" } ?s ?p ?o { "'])
def test_unknown_or_invalid_id_returns_none(store: OntologyStore, law_id: str) -> None:
    assert store.get_law(law_id) is None
    assert store.list_articles(law_id) is None


def test_articles_in_reading_order(store: OntologyStore) -> None:
    articles = store.list_articles("O0040001")

    assert articles is not None
    assert [a.number for a in articles] == ["7", "40"]
    assert articles[0].iri == "https://env.law/eli/tw/law/O0040001/2018-06-13/zho/art_7"


def test_promulgated_but_not_yet_in_force_has_no_articles(store: OntologyStore) -> None:
    # Promulgated on 2018-06-13, in force from the third day (中央法規標準法第 13 條).
    assert store.list_articles("O0040001", as_of="2018-06-14") == []


def test_a_date_before_the_held_versions_is_a_corpus_gap(store: OntologyStore) -> None:
    # The law existed in 2000, but the corpus holds no version covering that date.
    assert store.get_law("O0040001", as_of="2000-01-01") is None
    assert store.list_articles("O0040001", as_of="2000-01-01") is None
