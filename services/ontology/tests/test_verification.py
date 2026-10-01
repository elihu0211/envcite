"""Automatic verification (env-law-docs 02-05 §5.9): no one reads the statements.

Whether a statement becomes a fact is decided by gates and a policy over their records. The gates
here are deterministic tools; the tests run them on real schema rows, with no test double except
the one place a language model would be (the extractor)."""

import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from env_law_ontology.annotate import Loaded, load
from env_law_ontology.reasoning import evaluate
from env_law_ontology.verification import (
    ExecutableGate,
    NumbersGate,
    ReferencesGate,
    settle,
    verify,
)
from env_law_ontology.verification.calibration import ppi_mean
from env_law_ontology.verification.extract import extract_unit
from env_law_ontology.verification.gates import amounts, citations, cn_number
from tests.test_annotation import BEFORE, LAW, _doc, _limit
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.test_reasoning import WATER, WATER_DAY, _plain_duty

Conn = psycopg.Connection[tuple[object, ...]]

DETERMINISTIC = ["numbers", "references", "executable"]


def _review(conn: Conn, statement_id: int) -> str:
    return str(
        _scalar(conn, "SELECT review::text FROM annotation.statement WHERE id = %s", statement_id)
    )


def _latest(conn: Conn, statement_id: int, method: str) -> str:
    return str(
        _scalar(
            conn,
            "SELECT verdict::text FROM annotation.verification"
            " WHERE statement_id = %s AND method = %s ORDER BY id DESC LIMIT 1",
            statement_id,
            method,
        )
    )


def _record(conn: Conn, sid: int, method: str, verdict: str, verifier: str = "tool") -> None:
    conn.execute(
        "SELECT annotation.record_verification(%s, %s, %s, %s::annotation.verdict)",
        (sid, method, verifier, verdict),
    )
    conn.commit()


# ---- reading numbers and citations out of the text ----


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("六萬", 60000),
        ("二千萬", 20000000),
        ("一百", 100),
        ("六十萬", 600000),
        ("六千", 6000),
        ("一百零五", 105),
        ("十", 10),
        ("1,000", 1000),
        ("三億二千萬", 320000000),
    ],
)
def test_chinese_numbers(text: str, value: int) -> None:
    assert cn_number(text) == value


def test_the_amounts_and_their_bounds_are_read_from_a_penalty_sentence() -> None:
    assert amounts("處新臺幣六萬元以上二千萬元以下罰鍰，並通知限期改善") == [
        (60000, "以上"),
        (20000000, "以下"),
    ]


def test_citations_resolve_to_provision_paths() -> None:
    assert citations("違反第七條第一項或第八條規定者") == ["art_7/para_1", "art_8"]
    assert citations("依第五條之一第二項辦理") == ["art_5-1/para_2"]


# ---- the database: records and policy ----


def _two_statements(conn: Conn, tmp_path: Path) -> Loaded:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    return load(conn, _doc(_plain_duty(), review="extracted"))


def test_the_extractor_cannot_verify_its_own_statement(conn: Conn, tmp_path: Path) -> None:
    loaded = _two_statements(conn, tmp_path)
    duty = loaded.ids["duty"]

    with pytest.raises(psycopg.errors.CheckViolation, match="extractor cannot verify"):
        conn.execute(
            "SELECT annotation.record_verification(%s, 'x', 'test', 'pass')", (duty,)
        )  # agent of _doc is "test"
    conn.rollback()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("SELECT annotation.record_verification(-1, 'x', 'tool', 'pass')")
    conn.rollback()


def test_a_statement_is_confirmed_only_when_every_required_method_passes(
    conn: Conn, tmp_path: Path
) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]

    _record(conn, duty, "a", "pass")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "extracted"  # b is missing

    _record(conn, duty, "b", "inconclusive")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "extracted"  # b is not conclusive

    _record(conn, duty, "b", "pass")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (1, 0)
    assert _review(conn, duty) == "confirmed"


def test_nothing_passed_means_nothing_confirmed(conn: Conn, tmp_path: Path) -> None:
    """全部 not_applicable 不是通過：沒有任何方法檢查過它，就不接受。"""
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "not_applicable")

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "extracted"
    _record(conn, duty, "b", "pass")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (1, 0)


def test_one_failure_rejects_even_when_the_required_methods_pass(
    conn: Conn, tmp_path: Path
) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "pass")
    _record(conn, duty, "extra", "fail")  # 不在必要清單裡的方法失敗，同樣駁回

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (0, 1)
    assert _review(conn, duty) == "rejected"


def test_the_latest_verdict_of_a_method_is_the_one_that_counts(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "fail")
    _record(conn, duty, "a", "pass")  # 修好之後重驗

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (1, 0)


def test_every_settlement_is_recorded_with_its_policy(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "pass")
    conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])")
    conn.commit()

    row = conn.execute(
        "SELECT verifier, verdict::text, evidence FROM annotation.verification"
        " WHERE statement_id = %s AND method = 'policy'",
        (duty,),
    ).fetchone()
    assert row == ("settle", "pass", {"action": "confirmed", "required": ["a"]})


def test_an_empty_policy_is_refused(conn: Conn) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("SELECT * FROM annotation.settle(ARRAY[]::text[])")
    conn.rollback()


def test_settled_statements_are_not_settled_again(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "pass")
    conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])")
    _record(conn, duty, "a", "fail")  # 之後才失敗：不翻轉已確認的列（那是新一輪抽取與驗證的事）

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "confirmed"


# ---- the gates ----


def _water(conn: Conn, sample: None) -> Loaded:
    return load(conn, json.loads(WATER.read_text(encoding="utf-8")))


def test_numbers_gate_checks_the_amounts_against_the_text(conn: Conn, sample: None) -> None:
    ids = _water(conn, sample).ids
    gate = NumbersGate()

    assert gate.check(conn, ids["p40_1_fine"]).verdict == "pass"  # type: ignore[union-attr]
    assert gate.check(conn, ids["p40_2_fine"]).verdict == "pass"  # type: ignore[union-attr]
    assert gate.check(conn, ids["w7_1"]) is None  # 沒有罰則、沒有帶單位的門檻值

    conn.execute(
        "UPDATE annotation.sanction SET max_amount = 2000000 WHERE statement_id = %s",
        (ids["p40_1_fine"],),
    )
    result = gate.check(conn, ids["p40_1_fine"])
    assert result is not None and result.verdict == "fail"


def test_numbers_gate_fails_an_amount_the_text_does_not_have(conn: Conn, sample: None) -> None:
    ids = _water(conn, sample).ids
    conn.execute(
        "UPDATE annotation.sanction SET amount_text = '新臺幣一萬元罰鍰' WHERE statement_id = %s",
        (ids["p40_1_fine"],),
    )

    result = NumbersGate().check(conn, ids["p40_1_fine"])
    assert result is not None and result.verdict == "fail"


def test_references_gate_requires_the_punished_provision_to_be_the_cited_one(
    conn: Conn, sample: None
) -> None:
    ids = _water(conn, sample).ids
    gate = ReferencesGate()

    ok = gate.check(conn, ids["p40_1_fine"])
    assert ok is not None and ok.verdict == "pass"
    assert ok.evidence["unresolved"] == ["art_8"]  # 第八條不在語料裡：只記，不算錯

    conn.execute(
        "UPDATE annotation.sanction SET punishes_id = %s WHERE statement_id = %s",
        (ids["w7_2b"], ids["p40_1_fine"]),  # 第七條第二項：原文引用的是第一項
    )
    bad = gate.check(conn, ids["p40_1_fine"])
    assert bad is not None and bad.verdict == "fail"
    assert gate.check(conn, ids["w7_1"]) is None


def test_executable_gate_passes_a_duty_whose_verdicts_flip_where_the_conditions_say(
    conn: Conn, tmp_path: Path
) -> None:
    loaded = _two_statements(conn, tmp_path)

    result = ExecutableGate().check(conn, loaded.ids["duty"])

    assert result is not None and result.verdict == "pass"
    assert {c["case"] for c in result.evidence["cases"]} >= {"duty holds", "duty broken"}


def test_executable_gate_is_not_applicable_outside_obligations(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_limit("乙一", 100), review="extracted"))

    assert ExecutableGate().check(conn, loaded.ids["limit100"]) is None


def test_executable_gate_waits_for_the_limit_it_depends_on(conn: Conn, tmp_path: Path) -> None:
    """委任的限值所依的陳述還沒被接受時，門檻值解析不到：無法判定，不是失敗。"""
    from tests.test_annotation import _duty

    limit_text = "化學需氧量限值：每公升一百毫克"
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", limit_text)))
    loaded = load(conn, _doc(_duty(), _limit(limit_text, 100), review="extracted"))

    waiting = ExecutableGate().check(conn, loaded.ids["duty"])
    assert waiting is not None and waiting.verdict == "inconclusive"

    result = settle(conn, DETERMINISTIC)
    assert result.confirmed == 2  # 限值先被接受，委任的義務在下一輪才可判定
    assert result.rounds >= 2
    assert _review(conn, loaded.ids["duty"]) == "confirmed"


def test_executable_gate_does_not_guess_when_it_cannot_run_the_conditions(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    twice = _plain_duty()
    twice["conditions"].append(
        {"role": "scope", "clause": 1, "concept": "cod", "comparator": "ge", "threshold": 5}
    )
    loaded = load(conn, _doc(twice, review="extracted"))

    result = ExecutableGate().check(conn, loaded.ids["duty"])
    assert result is not None and result.verdict == "inconclusive"


# ---- the pipeline end to end ----


def test_verify_writes_one_row_per_gate_and_reruns_only_the_inconclusive(
    conn: Conn, sample: None
) -> None:
    ids = _water(conn, sample).ids

    first = verify(conn)
    second = verify(conn)

    assert first == 13 * 3  # 每個陳述、每個閘門一列（不適用也記）
    assert second <= first
    assert _latest(conn, ids["w7_2a"], "executable") == "not_applicable"


def test_the_water_draft_is_accepted_without_a_human_where_the_gates_can_decide(
    conn: Conn, sample: None
) -> None:
    ids = _water(conn, sample).ids

    result = settle(conn, DETERMINISTIC)

    # 義務陳述與十個罰則通過；兩個沒有任何閘門能檢查的陳述（授權、許可）維持 extracted。
    assert (result.confirmed, result.rejected) == (11, 0)
    assert _review(conn, ids["w7_1"]) == "confirmed"
    assert _review(conn, ids["p40_1_fine"]) == "confirmed"
    assert _review(conn, ids["w7_2a"]) == "extracted"
    assert _review(conn, ids["w7_2b"]) == "extracted"

    case = {
        "actor_is_regulated_entity": 1,
        "discharges_to_surface_water": 1,
        "actor_is_business_or_sewer_system": 1,
        "actor_is_livestock_business": 0,
        "improvement_overdue": 0,
        "effluent_meets_standard": 0,
        "circumstances_serious": 1,
        "revocation_necessary": 0,
    }
    verdicts = evaluate(conn, WATER_DAY, case).verdicts
    assert verdicts == {ids["w7_1"]: "violation"}  # 接受之後推論才看得到它


def test_a_tampered_statement_is_rejected_by_the_pipeline(conn: Conn, sample: None) -> None:
    ids = _water(conn, sample).ids
    conn.execute(
        "UPDATE annotation.sanction SET min_amount = 600000 WHERE statement_id = %s",
        (ids["p40_1_fine"],),
    )
    conn.commit()

    result = settle(conn, DETERMINISTIC)

    assert result.rejected == 1
    assert _review(conn, ids["p40_1_fine"]) == "rejected"
    assert evaluate(conn, WATER_DAY, {"effluent_meets_standard": 0}).penalties == ()


# ---- the extractor (a fake stands where a language model would) ----


class FakeModel:
    name = "fake:model"
    family = "fake"

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return (
            self.answer
            if isinstance(self.answer, str)
            else json.dumps(self.answer, ensure_ascii=False)
        )


EXTRACTED = {
    "concepts": [
        {"code": "discharging", "label": "排放廢水"},
        {"code": "compliant", "label": "符合標準"},
    ],
    "statements": [
        {
            "id": "s1",
            "quote": "排放廢水者應符合標準",
            "kind": "prescriptive",
            "deontic": "obligation",
            "conditions": [
                {
                    "role": "scope",
                    "clause": 1,
                    "concept": "discharging",
                    "comparator": "eq",
                    "threshold": 1,
                },
                {
                    "role": "duty",
                    "clause": 1,
                    "concept": "compliant",
                    "comparator": "eq",
                    "threshold": 1,
                },
            ],
        },
        {"id": "invented", "quote": "原文沒有這句話", "kind": "constitutive"},
    ],
}


def test_the_extractor_anchors_what_the_model_quotes_and_drops_what_it_invents(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = FakeModel(EXTRACTED)

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is not None and out.loaded.statements == 1
    assert out.dropped == ("invented: quote is not in the text",)
    assert "排放廢水者應符合標準" in model.prompts[0]
    assert _scalar(conn, "SELECT review::text FROM annotation.statement") == "extracted"
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


# ---- calibration against an external gold standard ----


def test_ppi_corrects_a_biased_proxy_and_widens_where_it_knows_less() -> None:
    truth = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]
    proxy = [min(1.0, t + 0.2) for t in truth]  # 代理系統性偏高
    unlabeled = [1.0] * 70 + [0.2] * 30  # 代理在大量樣本上的值

    est = ppi_mean(truth, proxy, unlabeled)

    naive = sum(unlabeled) / len(unlabeled)
    assert est.mean < naive  # 校正項把偏高拉回來
    assert est.low < est.mean < est.high


def test_ppi_interval_is_exactly_the_proxy_mean_when_the_proxy_is_perfect() -> None:
    truth = [1.0, 0.0, 1.0, 1.0]
    unlabeled = [1.0, 0.0, 1.0, 0.0, 1.0, 1.0]

    est = ppi_mean(truth, truth, unlabeled)

    assert est.mean == pytest.approx(sum(unlabeled) / len(unlabeled))


def test_ppi_needs_enough_samples() -> None:
    with pytest.raises(ValueError):
        ppi_mean([1.0], [1.0], [1.0, 0.0])
    with pytest.raises(ValueError):
        ppi_mean([1.0, 0.0], [1.0], [1.0, 0.0])


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


def test_a_model_answer_that_breaks_a_constraint_is_dropped_not_fatal(
    conn: Conn, tmp_path: Path
) -> None:
    """真實模型寫出過「constitutive 卻帶義務」這種違反約束的陳述：丟掉整份，記下，管線繼續。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    bad = json.loads(json.dumps(EXTRACTED))
    bad["statements"][0] |= {"kind": "constitutive", "deontic": "obligation"}

    out = extract_unit(
        conn,
        FakeModel(json.dumps(bad, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is None
    assert any(d.startswith("<invalid document:") for d in out.dropped)
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0


def test_a_penalty_that_punishes_nothing_in_the_unit_is_dropped_but_the_rest_is_kept(
    conn: Conn, tmp_path: Path
) -> None:
    """罰則所處罰的陳述在別的單元時，單元內連不上：只丟那一條，其餘照常寫入。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    doc["statements"] = [
        doc["statements"][0],
        {
            "id": "p1",
            "quote": "排放廢水者應符合標準",
            "kind": "penalty",
            "sanction": {"effect": "fine", "min": 1, "max": 2},
        },
    ]

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert out.dropped == ("p1: sanction punishes nothing in this unit",)


def test_an_exception_to_a_dropped_penalty_is_dropped_with_it(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    doc["statements"] = [
        doc["statements"][0],
        {
            "id": "p1",
            "quote": "排放廢水者應符合標準",
            "kind": "penalty",
            "sanction": {"effect": "fine", "punishes": "elsewhere"},
        },
        {"id": "e1", "quote": "排放廢水者", "kind": "constitutive", "exception_of": "p1"},
    ]

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert sorted(out.dropped) == [
        "e1: excepts nothing in this unit",
        "p1: sanction punishes nothing in this unit",
    ]
