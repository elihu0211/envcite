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


def test_sample_data_conforms_to_shapes() -> None:
    assert _validate(DATA.joinpath("laws.ttl").read_text(encoding="utf-8"))


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
