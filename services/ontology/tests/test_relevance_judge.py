"""The model relevance judges: the prompt, parsing the verdict, the conservative grade, agreement
(Cohen's κ, weighted κ) and calibration. No model is called except a loopback endpoint."""

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from envcite_ontology.verification.models import ChatCompletionsModel, ModelError
from tests.test_chat_adapter import Endpoint

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _tool() -> ModuleType:
    for name in ("golden_set", "relevance_judge"):
        spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules["relevance_judge"]


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    e = Endpoint()
    yield e
    e.server.shutdown()


GOOD = '{"intent": "罰鍰額度", "M": 3, "T": 3, "O": 2, "reason": "有罰則但夾雜他項"}'


@pytest.mark.parametrize(
    "reply",
    [
        GOOD,
        f"```json\n{GOOD}\n```",
        f"先想一想。\n{GOOD}\n以上。",
        GOOD.replace('"O": 2', '"O": "2"'),
    ],
)
def test_a_verdict_is_read_from_the_json_object_in_the_reply(reply: str) -> None:
    rj = _tool()
    v = rj.parse_verdict(reply)
    assert (v.M, v.T, v.O) == (3, 3, 2)
    assert v.reason == "有罰則但夾雜他項" and v.intent == "罰鍰額度"


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("沒有 JSON", "沒有 JSON"),
        ("{壞掉}", "合格的 JSON"),
        ("[1, 2]", "沒有 JSON"),
        (GOOD.replace('"O": 2', '"O": 4'), "O 不是 0–3"),
        (GOOD.replace('"O": 2', '"O": true'), "O 不是 0–3"),
        (GOOD.replace('"M": 3, ', ""), "M 不是 0–3"),
        (GOOD.replace('"O": 2', '"O": 2.5'), "O 不是 0–3"),
    ],
)
def test_a_reply_without_valid_scores_is_not_a_verdict(reply: str, message: str) -> None:
    rj = _tool()
    with pytest.raises(ValueError, match=message):
        rj.parse_verdict(reply)


def test_the_prompt_carries_the_question_the_article_and_its_version() -> None:
    rj = _tool()
    version = rj.version_note("2026-11-04", "2023-03-15", True)
    p = rj.render(" 罰多少？ ", "空氣污染防制法", "67", version, "處新臺幣…")
    assert "問題：罰多少？" in p
    assert "空氣污染防制法第67條（2026-11-04 現行的版本，公布或修正日 2023-03-15）" in p
    pending = rj.version_note("2026-11-04", "2026-07-15", False)
    assert pending == "2026-11-04 尚無確認施行的版本；以下是 2026-07-15 公布的最新版本"
    assert "處新臺幣…" in p
    assert '{"intent": "一句話", "M": 整數' in p  # 格式化沒有吃掉 JSON 的大括號
    assert len(rj.PROMPT_SHA256) == 64


def test_the_final_grade_is_the_lower_and_a_missing_grade_leaves_a_hole() -> None:
    rj = _tool()
    assert rj.conservative([3, 1]) == 1
    assert rj.conservative([2, 2]) == 2
    assert rj.conservative([3, None]) is None
    assert rj.conservative([]) is None


def test_cohen_kappa_matches_the_textbook_example() -> None:
    rj = _tool()
    # po = 3/4；兩人「1」的比例 1/2 與 1/4，pe = 1/2·1/4 + 1/2·3/4 = 1/2；κ = (3/4 − 1/2)/(1/2)
    assert rj.cohen_kappa([0, 1, 1, 0], [0, 1, 0, 0]) == pytest.approx(0.5)
    assert rj.cohen_kappa([0, 1], [0, 1]) == 1.0
    assert rj.cohen_kappa([1, 1], [1, 1]) is None  # 機率一致度為 1，κ 無定義
    assert rj.cohen_kappa([], []) is None
    with pytest.raises(ValueError):
        rj.cohen_kappa([1], [1, 0])


def test_weighted_kappa_charges_distant_disagreements_more() -> None:
    rj = _tool()
    assert rj.weighted_kappa([0, 1, 2, 3], [0, 1, 2, 3]) == pytest.approx(1.0)
    # 完全顛倒：觀察到的加權不一致 4，期望 2，κw = 1 − 4/2
    assert rj.weighted_kappa([0, 0, 3, 3], [3, 3, 0, 0]) == pytest.approx(-1.0)
    near = rj.weighted_kappa([0, 1, 2, 3], [1, 1, 2, 3])
    far = rj.weighted_kappa([0, 1, 2, 3], [3, 1, 2, 3])
    assert near is not None and far is not None and near > far
    # 二次加權 κ 與 sklearn cohen_kappa_score(weights="quadratic") 的值
    assert rj.weighted_kappa([0, 1, 2, 3, 2, 1], [0, 2, 2, 3, 1, 1]) == pytest.approx(9 / 11)


def test_agreement_counts_only_items_both_judges_graded() -> None:
    rj = _tool()
    got = rj.agreement({"a": [0, 3, 2, None, 1], "b": [0, 2, 1, 3, 1]})
    assert got["n"] == 4
    assert got["disagree_binary"] == 0.25  # (2, 1) 一個相關、一個不相關
    assert got["disagree_graded"] == 0.5
    assert got["confusion"]["3-2"] == 1 and got["confusion"]["2-1"] == 1
    assert got["cohen_kappa_binary"] == pytest.approx(0.5)


def test_negatives_are_other_articles_of_the_same_law_fixed_by_the_seed() -> None:
    rj = _tool()
    upstream = {"q1": ["A0000001-art-1", "A0000001-art-2"], "q2": ["B0000001-art-9"]}
    articles = {
        "A0000001": [f"A0000001-art-{i}" for i in range(1, 6)],
        "B0000001": ["B0000001-art-9"],
    }
    a = rj.negatives_for(upstream, articles, seed="s")
    assert a == rj.negatives_for(upstream, articles, seed="s")
    assert [q for q, _ in a] == ["q1", "q1"]  # q2 的法規沒有別的條
    picked = [d for _, d in a]
    assert len(set(picked)) == 2
    assert not set(picked) & set(upstream["q1"])
    assert all(d.startswith("A0000001-") for d in picked)


def _rows() -> list[dict[str, Any]]:
    return [
        {"label": "positive", "grades": {"a": 3, "b": 2}, "grade": 2},
        {"label": "positive", "grades": {"a": 2, "b": 1}, "grade": 1},
        {"label": "positive", "grades": {"a": None, "b": 3}, "grade": None},
        {"label": "negative", "grades": {"a": 2, "b": 0}, "grade": 0},
        {"label": "negative", "grades": {"a": 0, "b": 0}, "grade": 0},
    ]


def test_calibration_reports_recall_on_positives_and_false_positives_on_negatives() -> None:
    rj = _tool()
    got = rj.rates(_rows(), ["a", "b"], 2)
    assert got["a"]["positives"] == 2 and got["a"]["recall"] == 1.0
    assert got["a"]["false_positive_rate"] == 0.5
    assert got["b"]["recall"] == pytest.approx(2 / 3, abs=1e-4)
    assert got["b"]["false_positive_rate"] == 0.0
    assert got["conservative"]["recall"] == 0.5
    assert got["conservative"]["positive_grades"] == {"0": 0, "1": 1, "2": 1, "3": 0}


def _judge(rj: ModuleType, endpoint: Endpoint, model_id: str = "judge-x") -> Any:
    return rj.Judge(
        ChatCompletionsModel(
            model_id, family="fam", base_url=endpoint.url, api_key="k", max_retries=0
        )
    )


def test_a_judge_asks_for_the_schema_at_temperature_zero_and_counts_its_tokens(
    endpoint: Endpoint,
) -> None:
    rj = _tool()
    endpoint.reply = {
        "model": "judge-x",
        "choices": [{"message": {"content": GOOD}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    j = _judge(rj, endpoint)
    item = rj.Item("q1", "A0000001-art-1", "罰多少？", "某法", "2020-01-01", "條文")
    got = rj.ask(j, item, "2026-11-04")

    assert got["verdict"]["O"] == 2
    body = endpoint.requests[0]["body"]
    assert body["temperature"] == 0
    assert body["response_format"]["json_schema"]["schema"] == rj.SCHEMA
    assert j.usage == {"calls": 1, "prompt_tokens": 10, "completion_tokens": 3, "failures": 0}


def test_a_reply_from_another_model_is_a_failed_call_not_a_verdict(endpoint: Endpoint) -> None:
    rj = _tool()
    endpoint.reply = {"model": "fallback-y", "choices": [{"message": {"content": GOOD}}]}
    j = _judge(rj, endpoint)
    item = rj.Item("q1", "A0000001-art-1", "題", "某法", "2020-01-01", "條文")

    with pytest.raises(ModelError, match="fallback-y"):
        j.complete("x")
    got = rj.ask(j, item, "2026-11-04")
    assert "fallback-y" in got["error"] and "verdict" not in got
    assert j.usage["failures"] == 1


class FakeJudge:
    def __init__(self, name: str, grade: int | None) -> None:
        self.name = name
        self.grade = grade
        self.calls = 0

    def complete(self, prompt: str, *, response_format: Any = None) -> str:
        self.calls += 1
        if self.grade is None:
            return "不知道"
        return GOOD.replace('"O": 2', f'"O": {self.grade}')

    def failed(self) -> None:
        pass


def test_every_article_is_judged_once_per_judge_and_the_cache_resumes(tmp_path: Path) -> None:
    rj = _tool()
    a, b = FakeJudge("fam-a:a", 3), FakeJudge("fam-b:b", 1)
    items = [
        rj.Item("q1", f"A0000001-art-{i}", "題", "某法", "2020-01-01", f"條文{i}") for i in (1, 2)
    ]
    cache = rj.Cache(tmp_path / "c.jsonl")
    out = rj.judge_all(items, [a, b], cache, as_of="2026-11-04", workers=2, log=lambda _: None)
    assert a.calls == b.calls == 2
    rows = rj.rows_of(items, out, [a, b], {"q1": {"A0000001-art-1"}})
    assert [r["grade"] for r in rows] == [1, 1]
    assert rows[0]["upstream"] and not rows[1]["upstream"]

    again = rj.judge_all(
        items,
        [a, b],
        rj.Cache(tmp_path / "c.jsonl"),
        as_of="2026-11-04",
        workers=2,
        log=lambda _: None,
    )
    assert a.calls == b.calls == 2  # 全部命中快取
    assert again == out
    lines = (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()
    assert all("key" in json.loads(line) for line in lines)


def test_a_judge_that_cannot_answer_leaves_the_article_a_hole(tmp_path: Path) -> None:
    rj = _tool()
    a, b = FakeJudge("fam-a:a", 2), FakeJudge("fam-b:b", None)
    items = [rj.Item("q1", "A0000001-art-1", "題", "某法", "2020-01-01", "條文")]
    out = rj.judge_all(
        items,
        [a, b],
        rj.Cache(tmp_path / "c.jsonl"),
        as_of="2026-11-04",
        workers=1,
        log=lambda _: None,
    )
    assert b.calls == rj.PARSE_RETRIES + 1
    (row,) = rj.rows_of(items, out, [a, b], {})
    assert row["grade"] is None and row["grades"] == {"fam-a:a": 2, "fam-b:b": None}
    assert "沒有 JSON" in row["errors"]["fam-b:b"]


def test_a_judge_that_keeps_failing_is_no_longer_asked_and_its_articles_stay_unjudged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rj = _tool()
    monkeypatch.setattr(rj, "BREAKER", 2)
    a, b = FakeJudge("fam-a:a", 2), FakeJudge("fam-b:b", None)
    items = [
        rj.Item("q1", f"A0000001-art-{i}", "題", "某法", "2020-01-01", f"條文{i}") for i in range(6)
    ]
    cache = rj.Cache(tmp_path / "c.jsonl")
    out = rj.judge_all(items, [a, b], cache, as_of="2026-11-04", workers=1, log=lambda _: None)

    assert a.calls == 6  # 另一位不受影響
    assert b.calls == 2 * (rj.PARSE_RETRIES + 1)
    rows = rj.rows_of(items, out, [a, b], {})
    assert all(r["grade"] is None for r in rows)
    assert sum("fam-b:b" in json.loads(line)["judge"] for line in cache.path.open()) == 2
