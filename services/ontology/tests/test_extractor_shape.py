"""What the extractor does with answers of the wrong shape: fences, constraints, bad
conditions, missing directions."""

import json
from pathlib import Path

from envcite_ontology.verification.extract import (
    completeness_issues,
    extract_unit,
)
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import (
    EXTRACTED,
    Conn,
    FakeModel,
    Sequence_,
)


def test_the_extractor_reads_json_that_the_model_wrapped_in_a_code_fence(
    conn: Conn, tmp_path: Path
) -> None:
    """真實的模型常把 JSON 包在 ```json 圍欄裡，前後還有說明。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    fenced = (
        "好的，以下是標註：\n```json\n"
        + json.dumps(EXTRACTED, ensure_ascii=False)
        + "\n```\n以上。"
    )

    out = extract_unit(conn, FakeModel(fenced), LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is not None and out.loaded.statements == 1


def test_a_broader_the_model_adds_to_a_concept_is_ignored(conn: Conn, tmp_path: Path) -> None:
    """提示沒要 broader；模型多寫了也不該讓載入器拒絕整份。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    answer = json.loads(json.dumps(EXTRACTED))
    for c in answer["concepts"]:
        c["broader"] = "anything"

    out = extract_unit(
        conn,
        FakeModel(json.dumps(answer, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1


def test_a_model_answer_that_breaks_a_constraint_is_dropped_not_fatal(
    conn: Conn, tmp_path: Path
) -> None:
    """真實模型引用過自己沒定義的概念：先請它修；修不好，只丟那個陳述，記下，管線繼續。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    bad = json.loads(json.dumps(EXTRACTED))
    bad["concepts"] = []
    model = FakeModel(json.dumps(bad, ensure_ascii=False))

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is None
    assert any("malformed conditions" in d for d in out.dropped)
    assert any("沒有在 concepts 裡宣告" in p for p in model.prompts[1:])  # 修復提示指出了問題
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0


def test_a_statement_with_a_bad_condition_does_not_take_the_good_ones_down(
    conn: Conn, tmp_path: Path
) -> None:
    """一個單元裡一個陳述的要件沒有 concept：只丟它，同單元其餘的陳述照樣寫入。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    good = doc["statements"][0]
    bad = {
        "id": "bad",
        "quote": "排放廢水者",
        "kind": "constitutive",
        "conditions": [{"role": "scope", "concept": None, "comparator": "eq", "threshold": 1}],
    }
    doc["statements"] = [good, bad]

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and set(out.loaded.ids) == {"s1"}
    assert any(d.startswith("bad:") and "malformed conditions" in d for d in out.dropped)


def test_a_condition_without_a_concept_is_repaired_when_the_model_names_it(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    broken = json.loads(json.dumps(EXTRACTED))
    broken["statements"][0]["conditions"][0]["concept"] = None
    model = Sequence_(broken, EXTRACTED)

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is not None and out.loaded.statements == 1
    assert "沒有 concept" in model.prompts[1]


def test_a_slot_written_as_a_plain_string_is_its_literal(conn: Conn, tmp_path: Path) -> None:
    """真實模型常把槽位寫成 `"subject": "事業"`：字串無歧義就是字面，照收，不丟。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    doc["statements"][0]["slots"] = {"subject": "排放廢水者"}

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert _scalar(conn, "SELECT literal FROM annotation.statement_slot") == "排放廢水者"


def test_a_prescriptive_statement_that_stays_without_a_direction_is_not_written(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    bad = json.loads(json.dumps(EXTRACTED))
    bad["statements"][0].pop("deontic")

    out = extract_unit(
        conn,
        FakeModel(json.dumps(bad, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is None
    assert "s1: prescriptive without a direction after repair" in out.dropped


def test_a_deontic_on_a_statement_that_is_not_prescriptive_is_removed(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    odd = json.loads(json.dumps(EXTRACTED))
    odd["statements"][0] |= {"kind": "constitutive"}

    out = extract_unit(
        conn,
        FakeModel(json.dumps(odd, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert (
        _scalar(conn, "SELECT count(*) FROM annotation.statement WHERE norm_modality IS NOT NULL")
        == 0
    )


def test_a_prescriptive_statement_without_a_direction_is_incomplete() -> None:
    assert completeness_issues([{"id": "a", "kind": "prescriptive"}]) != []
