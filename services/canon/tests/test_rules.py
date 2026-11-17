"""The deterministic-rule extractors share one identity, one result type and one runner."""

import psycopg

from canon import rules, sync, violation
from canon.law import delegation
from canon.rules import Extracted, Extractor

Conn = psycopg.Connection[tuple[object, ...]]


def test_an_extractor_names_the_derivation_it_records() -> None:
    extractor = Extractor("canon-example-rule", 3, lambda conn: Extracted(0))

    assert extractor.derivation == {
        "method": "rule",
        "agent": "canon-example-rule",
        "agent_version": "3",
    }


def test_the_registered_extractors_have_unique_names_and_positive_versions() -> None:
    names = [e.name for e in sync.EXTRACTORS]

    assert len(names) == len(set(names))
    assert all(e.version > 0 for e in sync.EXTRACTORS)
    assert {delegation.EXTRACTOR, violation.EXTRACTOR} == set(sync.EXTRACTORS)


def test_run_calls_each_extractor_in_order_and_returns_the_results_by_name(conn: Conn) -> None:
    calls: list[str] = []

    def make(name: str, written: int) -> Extractor:
        def extract(_: Conn) -> Extracted:
            calls.append(name)
            return Extracted(written, unresolved=1, skipped=2)

        return Extractor(name, 1, extract)

    results = rules.run(conn, make("first", 5), make("second", 7))

    assert calls == ["first", "second"]
    assert results == {
        "first": Extracted(5, 1, 2),
        "second": Extracted(7, 1, 2),
    }
