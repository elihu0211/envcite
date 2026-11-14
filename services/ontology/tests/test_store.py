import pytest

from envcite_ontology.store.ontology_store import OntologyStore
from envcite_ontology.store.records import LawKey

pytestmark = pytest.mark.usefixtures("sample")


def _moj(pcode: str) -> LawKey:
    return LawKey("TW-CENTRAL", "moj_pcode", pcode)


def test_get_law_reads_title_from_expression(store: OntologyStore) -> None:
    law = store.laws.get_law(_moj("O0020001"))

    assert law is not None
    assert law.title == "空氣污染防制法"
    assert law.language == "zho"
    assert law.date_document == "1975-05-23"
    assert law.iri == "https://envcite/tw/law/O0020001"


@pytest.mark.parametrize("law_id", ["Z9999999", "", "o0020001", 'x" } ?s ?p ?o { "'])
def test_unknown_or_invalid_id_returns_none(store: OntologyStore, law_id: str) -> None:
    assert store.laws.get_law(_moj(law_id)) is None
    assert store.laws.list_articles(_moj(law_id)) is None


def test_articles_in_reading_order(store: OntologyStore) -> None:
    articles = store.laws.list_articles(_moj("O0040001"))

    assert articles is not None
    assert [a.number for a in articles] == ["7", "40"]
    assert articles[0].iri == "https://envcite/tw/law/O0040001/2018-06-13/zho/art_7"


def test_promulgated_but_not_yet_in_force_has_no_articles(store: OntologyStore) -> None:
    # Promulgated on 2018-06-13, in force from the third day (中央法規標準法第 13 條).
    assert store.laws.list_articles(_moj("O0040001"), as_of="2018-06-14") == []


def test_a_date_before_the_held_versions_is_a_corpus_gap(store: OntologyStore) -> None:
    # The law existed in 2000, but the corpus holds no version covering that date.
    assert store.laws.get_law(_moj("O0040001"), as_of="2000-01-01") is None
    assert store.laws.list_articles(_moj("O0040001"), as_of="2000-01-01") is None


def test_the_same_local_id_under_another_jurisdiction_is_another_law(store: OntologyStore) -> None:
    """自然鍵含管轄：同一個在地識別碼換了管轄就不是同一部法規。"""
    assert store.laws.get_law(LawKey("TW-TPE", "local_glrs", "O0020001")) is None
    assert not store.laws.law_exists(LawKey("TW-TPE", "local_glrs", "O0020001"))
    assert store.laws.law_exists(_moj("O0020001"))


def test_the_closed_sets_come_from_the_schema(store: OntologyStore) -> None:
    assert store.laws.id_schemes == {"moj_pcode", "local_glrs"}
    assert "TW-CENTRAL" in store.laws.jurisdictions
    assert len(store.laws.jurisdictions) == 23
