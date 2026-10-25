from envcite_consult import pipeline
from envcite_consult.ports import Article
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
    empty = pipeline.assess([], [])
    irrelevant_input = [article(1, 0.1)]
    irrelevant = pipeline.assess(pipeline.ground(irrelevant_input), irrelevant_input)

    assert empty is not None and empty.reason is pipeline.RefusalReason.NO_EVIDENCE
    assert irrelevant is not None and irrelevant.reason is pipeline.RefusalReason.OUT_OF_SCOPE


def test_blank_question_is_refused_before_retrieval() -> None:
    ontology = FakeOntology([article(1, 0.9)])

    outcome = pipeline.run("   ", as_of="", ontology=ontology)

    assert isinstance(outcome, pipeline.Refusal)
    assert outcome.reason is pipeline.RefusalReason.MISSING_INPUT
    assert ontology.searches == []


def test_confidence_follows_the_strongest_grounded_source() -> None:
    weak = [article(1, 0.35)]
    strong = [article(1, 0.9)]

    assert pipeline.confidence_of(pipeline.ground(weak), weak) is pipeline.Confidence.LOW
    assert pipeline.confidence_of(pipeline.ground(strong), strong) is pipeline.Confidence.HIGH


def test_run_returns_evidence_graded_by_its_own_retrieval() -> None:
    outcome = pipeline.run(
        "問句", as_of="", ontology=FakeOntology([article(1, 0.35), article(2, 0.1)])
    )

    assert isinstance(outcome, pipeline.Evidence)
    assert [c.code for c in outcome.citations] == ["L1"]
    assert outcome.confidence is pipeline.Confidence.LOW


def test_run_refuses_when_nothing_is_retrieved() -> None:
    outcome = pipeline.run("問句", as_of="", ontology=FakeOntology([]))

    assert isinstance(outcome, pipeline.Refusal)
    assert outcome.reason is pipeline.RefusalReason.NO_EVIDENCE


def test_search_result_order_is_preserved_by_grounding() -> None:
    articles: list[Article] = [article(10, 0.9), article(2, 0.8)]

    assert [c.label for c in pipeline.ground(articles)] == [
        "空氣污染防制法 第 10 條",
        "空氣污染防制法 第 2 條",
    ]


def test_ground_keeps_articles_qualifying_via_semantic_similarity() -> None:
    # 純字面分數未達 0.30，但語意相似度達 0.80（>= 0.65）之條文應被保留
    semantic_hit = Article(
        iri="https://law.example.org/a1",
        label="空氣污染防制法 第 5 條",
        text="條文內容",
        score=0.05,
        lexical_score=0.05,
        semantic_similarity=0.80,
    )
    irrelevant_hit = Article(
        iri="https://law.example.org/a2",
        label="空氣污染防制法 第 6 條",
        text="條文內容",
        score=0.10,
        lexical_score=0.10,
        semantic_similarity=0.45,
    )

    grounding = pipeline.ground([semantic_hit, irrelevant_hit])

    assert len(grounding) == 1
    assert grounding[0].code == "L1"
    assert grounding[0].iri == "https://law.example.org/a1"


def test_confidence_high_when_top_semantic_similarity_reaches_high_floor() -> None:
    semantic_hit = Article(
        iri="https://law.example.org/a1",
        label="空氣污染防制法 第 5 條",
        text="條文內容",
        score=0.35,
        lexical_score=0.35,
        semantic_similarity=0.78,
    )
    grounding = pipeline.ground([semantic_hit])

    assert pipeline.confidence_of(grounding, [semantic_hit]) is pipeline.Confidence.HIGH
