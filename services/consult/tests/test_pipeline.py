from collections.abc import Iterator

from env_law.consult import pipeline
from env_law.consult.ports import Article
from tests.conftest import FakeOntology, article


def test_ground_assigns_codes_in_retrieval_order() -> None:
    grounding = pipeline.ground([article(1, 0.9), article(2, 0.8)])

    assert [c.code for c in grounding] == ["L1", "L2"]


def test_ground_drops_below_the_relevance_floor() -> None:
    grounding = pipeline.ground([article(1, 0.9), article(2, 0.1)])

    assert [c.code for c in grounding] == ["L1"]


def test_ground_caps_the_closed_set() -> None:
    many = [article(n, 0.9) for n in range(1, pipeline.MAX_CITATIONS + 5)]

    assert len(pipeline.ground(many)) == pipeline.MAX_CITATIONS


def test_nothing_retrieved_and_nothing_relevant_are_different_refusals() -> None:
    empty = pipeline.assess("問句", [], [])
    irrelevant_input = [article(1, 0.1)]
    irrelevant = pipeline.assess("問句", pipeline.ground(irrelevant_input), irrelevant_input)

    assert empty is not None and empty.reason is pipeline.RefusalReason.NO_EVIDENCE
    assert irrelevant is not None and irrelevant.reason is pipeline.RefusalReason.OUT_OF_SCOPE


def test_blank_question_is_missing_input() -> None:
    refusal = pipeline.assess("   ", [], [article(1, 0.9)])

    assert refusal is not None and refusal.reason is pipeline.RefusalReason.MISSING_INPUT


def test_confidence_follows_the_strongest_grounded_source() -> None:
    weak = [article(1, 0.35)]
    strong = [article(1, 0.9)]

    assert pipeline.confidence_of(pipeline.ground(weak), weak) is pipeline.Confidence.LOW
    assert pipeline.confidence_of(pipeline.ground(strong), strong) is pipeline.Confidence.HIGH


def test_cited_codes_ignores_codes_the_model_invented() -> None:
    grounding = pipeline.ground([article(1, 0.9)])

    assert pipeline.cited_codes("見 [L1] 與 [L9]", grounding) == ["L1"]


def test_refusal_stops_before_the_model_runs() -> None:
    class ExplodingLlm:
        def generate(
            self, *, question: str, evidence: str, allowed_codes: list[str]
        ) -> Iterator[str]:
            raise AssertionError("the model must not run when the pipeline refuses")

    outcome = pipeline.run("問句", as_of="", ontology=FakeOntology([]), llm=ExplodingLlm())

    assert outcome.refusal is not None
    assert outcome.chunks is None


def test_evidence_block_carries_only_codes_and_text() -> None:
    grounding = pipeline.ground([article(1, 0.9)])
    block = pipeline.render_evidence(grounding)

    assert block.startswith("[L1] 空氣污染防制法 第 1 條")


def test_search_result_order_is_preserved_by_grounding() -> None:
    articles: list[Article] = [article(10, 0.9), article(2, 0.8)]

    assert [c.label for c in pipeline.ground(articles)] == [
        "空氣污染防制法 第 10 條",
        "空氣污染防制法 第 2 條",
    ]
