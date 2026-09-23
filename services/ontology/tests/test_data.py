"""Regression tests for the SHACL shapes themselves.

The shapes are not applied to any live data: the relational tables are the truth and
the ontology projection does not exist yet (see env-law-docs 02-02). They are kept,
under negative tests, so this ELI conformance spec does not rot before the projection
lands. The positive case went away with the sample Turtle — that data now lives as a
SQL seed, and keeping both would be two truths.
"""

from importlib.resources import files

import pyshacl
import pytest

DATA = files("env_law.data")


def _validate(data: str) -> bool:
    conforms, _, _ = pyshacl.validate(
        data,
        shacl_graph=DATA.joinpath("shapes.ttl").read_text(encoding="utf-8"),
        inference="none",
        data_graph_format="turtle",
        shacl_graph_format="turtle",
    )
    return bool(conforms)


@pytest.mark.parametrize(
    "bad",
    [
        # law without an expression (no title)
        "<https://x/l> a <http://data.europa.eu/eli/ontology#LegalResource> ; "
        '<http://data.europa.eu/eli/ontology#id_local> "l" .',
        # article number not in "N" or "N-M" form
        "<https://x/a> a <http://data.europa.eu/eli/ontology#LegalResourceSubdivision> ; "
        '<http://data.europa.eu/eli/ontology#number> "第一條" .',
    ],
)
def test_shapes_reject_invalid_data(bad: str) -> None:
    assert not _validate(bad)
