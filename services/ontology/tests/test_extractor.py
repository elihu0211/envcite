"""The extractor, with a fake standing where a language model would (env-law-docs 02-06 §3.3)."""

from pathlib import Path

from env_law_ontology.reasoning import evaluate
from env_law_ontology.verification import (
    settle,
)
from env_law_ontology.verification.extract import (
    extract_unit,
)
from tests.test_annotation import BEFORE, LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import (
    DETERMINISTIC,
    EXTRACTED,
    Conn,
    FakeModel,
)


def test_the_extractor_anchors_what_the_model_quotes_and_drops_what_it_invents(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = FakeModel(EXTRACTED)

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is not None and out.loaded.statements == 1
    assert out.dropped == ("invented: quote is not in the text",)
    assert "排放廢水者應符合標準" in model.prompts[0]
    assert _scalar(conn, "SELECT confirm_state::text FROM annotation.statement") == "extracted"
    assert _scalar(conn, "SELECT method::text FROM annotation.derivation") == "llm"


def test_an_unparseable_model_answer_writes_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))

    out = extract_unit(
        conn, FakeModel("not json"), LAW, "art_1", agent="fake-model", agent_version="1"
    )

    assert out.loaded is None
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0


def test_from_text_to_conclusion_without_a_human(conn: Conn, tmp_path: Path) -> None:
    """抽取 → 驗證 → 接受 → 推論：中間沒有人。模型是替身；其餘是真的。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    extract_unit(conn, FakeModel(EXTRACTED), LAW, "art_1", agent="fake-model", agent_version="1")

    assert evaluate(conn, BEFORE, {"discharging": 1, "compliant": 0}).verdicts == {}  # 尚未被接受
    settle(conn, DETERMINISTIC)
    after = evaluate(conn, BEFORE, {"discharging": 1, "compliant": 0})

    assert list(after.verdicts.values()) == ["violation"]
