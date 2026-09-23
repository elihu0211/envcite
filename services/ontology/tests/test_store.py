import pytest

from env_law.store import OntologyStore


def test_get_law_reads_title_from_expression(store: OntologyStore) -> None:
    law = store.get_law("air-pollution-control-act")

    assert law is not None
    assert law.title == "空氣污染防制法"
    assert law.language == "zh-hant"
    assert law.date_document == "2000-01-01"
    assert law.iri == "https://env.law/eli/tw/law/air-pollution-control-act"


def test_get_law_without_date(store: OntologyStore) -> None:
    law = store.get_law("water-pollution-control-act")

    assert law is not None
    assert law.date_document == ""


@pytest.mark.parametrize("law_id", ["unknown-act", "", 'x" } ?s ?p ?o { "', "UPPER"])
def test_unknown_or_invalid_id_returns_none(store: OntologyStore, law_id: str) -> None:
    assert store.get_law(law_id) is None
    assert store.list_articles(law_id) is None


def test_articles_in_reading_order(store: OntologyStore) -> None:
    articles = store.list_articles("air-pollution-control-act")

    assert articles is not None
    assert [a.number for a in articles] == ["1", "2", "5-1", "10"]


def test_law_without_articles(store: OntologyStore) -> None:
    assert store.list_articles("water-pollution-control-act") == []
