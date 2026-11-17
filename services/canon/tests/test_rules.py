"""The deterministic-rule extractors share one identity, one result type and one runner."""

import pytest

from canon import rules, sync, violation
from canon.db import Conn
from canon.law import delegation, judgment_anchor, judgment_sync
from canon.law.guidance_sync import provenance
from canon.rules import Extracted, Extractor, Rule


def test_an_extractor_names_the_derivation_it_records() -> None:
    extractor = Extractor("canon-example-rule", 3, lambda conn: Extracted(0))

    assert extractor.derivation == {
        "method": "rule",
        "agent": "canon-example-rule",
        "agent_version": "3",
    }


def test_a_rule_that_loads_documents_names_its_derivation_without_an_extract() -> None:
    rule = Rule("canon-example-rule", 2)

    assert rule.derivation == {
        "method": "rule",
        "agent": "canon-example-rule",
        "agent_version": "2",
    }
    assert isinstance(delegation.EXTRACTOR, Rule)


def test_a_version_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="version must be positive"):
        Rule("canon-example-rule", 0)


def test_guidance_and_judgment_record_the_derivations_they_always_did() -> None:
    """The text stored in `derivation.producer_version` does not change with the int version."""
    assert provenance.DERIVATION == {
        "method": "rule",
        "agent": "canon-guidance-sync",
        "agent_version": "2",
    }
    assert judgment_sync.RULE.derivation == {
        "method": "rule",
        "agent": "canon-judicial-sync",
        "agent_version": "1",
    }


def test_the_judgment_derivation_follows_the_anchor_rule_version() -> None:
    """Bumping the extraction rule must change what the loaded decisions are labelled with."""
    assert judgment_sync.RULE.version == judgment_anchor.RULE_VERSION


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
