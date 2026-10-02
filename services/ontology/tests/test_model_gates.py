"""Model gates and the whole pipeline (env-law-docs 02-05 §5.9).

No real model runs here: this environment has no credentials, and a test that called one would
not be a test. The models are scripted fakes with a name and a family, which is all a gate may
know about them. What is real: the schema, the SQL functions, the engine, the policy, and the
rules that decide which verifier counts."""

import json
import re
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest

from env_law_ontology.reasoning import evaluate
from env_law_ontology.verification import (
    AnthropicModel,
    IndependentTestGate,
    PanelGate,
    RoundtripGate,
    build_model,
)
from env_law_ontology.verification.extract import extract_unit
from env_law_ontology.verification.gates import GateResult, ReferencesGate
from env_law_ontology.verification.model_gates import REFORMALIZE, render_annotation
from env_law_ontology.verification.run import (
    ALL_METHODS,
    PROBES,
    SameModel,
    assert_distinct,
    run,
    units_of,
)
from tests.test_annotation import BEFORE, LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.test_verification import EXTRACTED

Conn = psycopg.Connection[tuple[object, ...]]


class Scripted:
    """A model with a name, a family, and a reply — all a gate may know about it."""

    def __init__(self, name: str, reply: str | dict[str, Any] | Callable[[str], str]) -> None:
        self.name = name
        self.family = name.split(":", 1)[0]
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if callable(self.reply):
            return self.reply(prompt)
        return (
            self.reply
            if isinstance(self.reply, str)
            else json.dumps(self.reply, ensure_ascii=False)
        )


EXTRACTOR = "fake-a:extractor"
TEXT = "排放廢水者應符合標準"

SAME_AS_ANNOTATION = {
    "kind": "prescriptive",
    "deontic": "obligation",
    "conditions": [
        {
            "role": "scope",
            "code": "discharging",
            "comparator": "eq",
            "threshold": 1,
            "negated": False,
            "judgement": False,
        },
        {
            "role": "duty",
            "code": "compliant",
            "comparator": "eq",
            "threshold": 1,
            "negated": False,
            "judgement": False,
        },
    ],
    "sanction": None,
}
CASES = {
    "cases": [
        {"facts": {"discharging": 1, "compliant": 1}, "expected": "compliant"},
        {"facts": {"discharging": 1, "compliant": 0}, "expected": "violation"},
        {"facts": {"discharging": 0, "compliant": 1}, "expected": "not_applicable"},
    ]
}
FAITHFUL = {"faithful": True, "issues": []}
UNFAITHFUL = {"faithful": False, "issues": ["義務方向不對"]}


def _extracted(conn: Conn, tmp_path: Path) -> int:
    """One law, one extracted (unsettled) statement, written by `fake-a:extractor`."""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    out = extract_unit(conn, Scripted(EXTRACTOR, EXTRACTED), LAW, "art_1", agent_version="1")
    assert out.loaded is not None
    return out.loaded.ids["s1"]


# ---- the description every verifier reads ----


def test_the_description_is_deterministic_and_carries_the_concept_codes(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)

    text = render_annotation(conn, sid)

    assert text == render_annotation(conn, sid)
    assert "規範性陳述・義務" in text
    assert "[discharging] 排放廢水 成立" in text and "[compliant] 符合標準 成立" in text


# ---- round trip ----


def test_the_roundtrip_passes_when_another_reader_rebuilds_the_same_annotation(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    reader = Scripted("fake-b:reader", SAME_AS_ANNOTATION)

    result = RoundtripGate(reader).check(conn, sid)

    assert result is not None and result.verdict == "pass"
    assert TEXT not in reader.prompts[0]  # 它看不到原文


def test_the_roundtrip_fails_and_names_what_differs(conn: Conn, tmp_path: Path) -> None:
    sid = _extracted(conn, tmp_path)
    broken = json.loads(json.dumps(SAME_AS_ANNOTATION))
    broken["conditions"][1]["comparator"] = "ne"
    broken["deontic"] = "permission"

    result = RoundtripGate(Scripted("fake-b:reader", broken)).check(conn, sid)

    assert result is not None and result.verdict == "fail"
    assert set(result.evidence["differences"]) == {"deontic", "conditions"}


def test_a_direction_the_reader_adds_to_a_constitutive_statement_is_ignored(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    answer = {"concepts": [], "statements": [{"id": "d", "quote": TEXT, "kind": "constitutive"}]}
    out = extract_unit(conn, Scripted(EXTRACTOR, answer), LAW, "art_1", agent_version="1")
    assert out.loaded is not None
    reads_authority_as_permission = {
        "kind": "constitutive",
        "deontic": "permission",
        "conditions": [],
    }

    result = RoundtripGate(Scripted("fake-b:reader", reads_authority_as_permission)).check(
        conn, out.loaded.ids["d"]
    )

    assert result is not None and result.verdict == "pass"


def test_the_reader_is_told_only_prescriptive_statements_have_a_direction() -> None:
    assert "只有 kind 為 prescriptive 才有 deontic" in REFORMALIZE


@pytest.mark.parametrize("answer", ["not json", {"kind": None}])
def test_an_unusable_roundtrip_answer_is_not_a_pass(
    conn: Conn, tmp_path: Path, answer: Any
) -> None:
    sid = _extracted(conn, tmp_path)

    result = RoundtripGate(Scripted("fake-b:reader", answer)).check(conn, sid)

    assert result is not None and result.verdict in {"inconclusive", "fail"}


def test_a_verifier_from_the_extractors_family_does_not_count(conn: Conn, tmp_path: Path) -> None:
    sid = _extracted(conn, tmp_path)
    cousin = Scripted("fake-a:reader", SAME_AS_ANNOTATION)

    refused = RoundtripGate(cousin).check(conn, sid)
    allowed = RoundtripGate(cousin, allow_same_family=True).check(conn, sid)

    assert refused is not None and refused.verdict == "inconclusive"
    assert refused.evidence["skipped"] == ["fake-a:reader"]
    assert cousin.prompts and allowed is not None and allowed.verdict == "pass"  # 明說才採計
    # 抽取者自己永遠不行，不論允不允許同家族。
    twin = RoundtripGate(Scripted(EXTRACTOR, SAME_AS_ANNOTATION), allow_same_family=True).check(
        conn, sid
    )
    assert twin is not None and twin.verdict == "inconclusive"


# ---- independent tests ----


def test_independent_cases_run_through_the_engine_and_must_agree(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    tester = Scripted("fake-b:tester", CASES)

    result = IndependentTestGate(tester).check(conn, sid)

    assert result is not None and result.verdict == "pass"
    assert result.evidence["agree"] == 3
    assert (
        TEXT in tester.prompts[0] and "規範性陳述" not in tester.prompts[0]
    )  # 只給原文與概念，不給標註


def test_the_tester_is_told_which_concepts_are_yes_or_no_and_which_are_numbers(
    conn: Conn, tmp_path: Path
) -> None:
    """不說型別，測試設計者會對成立與否的概念編出數值，與標註對不上。"""
    sid = _extracted(conn, tmp_path)
    tester = Scripted("fake-b:tester", CASES)

    IndependentTestGate(tester).check(conn, sid)

    assert "discharging：排放廢水（成立與否，成立為 1、不成立為 0）" in tester.prompts[0]
    assert "compliant：符合標準（成立與否，成立為 1、不成立為 0）" in tester.prompts[0]


def test_independent_cases_that_all_disagree_fail_and_a_mix_is_inconclusive(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    flipped = {
        "cases": [
            {"facts": {"discharging": 1, "compliant": 1}, "expected": "violation"},
            {"facts": {"discharging": 1, "compliant": 0}, "expected": "compliant"},
            {"facts": {"discharging": 0, "compliant": 1}, "expected": "violation"},
        ]
    }
    mixed = {"cases": [*CASES["cases"][:2], flipped["cases"][2]]}

    failed = IndependentTestGate(Scripted("fake-b:t", flipped)).check(conn, sid)
    undecided = IndependentTestGate(Scripted("fake-b:t", mixed)).check(conn, sid)
    assert failed is not None and failed.verdict == "fail"
    assert undecided is not None and undecided.verdict == "inconclusive"
    few = IndependentTestGate(Scripted("fake-b:t", {"cases": CASES["cases"][:2]})).check(conn, sid)
    assert few is not None and few.verdict == "inconclusive"


def test_independent_tests_do_not_apply_to_other_kinds_of_statement(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    answer = {"concepts": [], "statements": [{"id": "d", "quote": TEXT, "kind": "constitutive"}]}
    out = extract_unit(conn, Scripted(EXTRACTOR, answer), LAW, "art_1", agent_version="1")
    assert out.loaded is not None

    assert IndependentTestGate(Scripted("fake-b:t", CASES)).check(conn, out.loaded.ids["d"]) is None


# ---- the panel ----


def _panel(conn: Conn, tmp_path: Path, *votes: tuple[str, Any]) -> Any:
    sid = _extracted(conn, tmp_path)
    judges = tuple(Scripted(name, answer) for name, answer in votes)
    return PanelGate(judges).check(conn, sid)


def test_the_panel_decides_by_a_two_thirds_majority_across_families(
    conn: Conn, tmp_path: Path
) -> None:
    yes = _panel(
        conn, tmp_path, ("fake-b:1", FAITHFUL), ("fake-c:1", FAITHFUL), ("fake-d:1", UNFAITHFUL)
    )
    assert yes.verdict == "pass"


def test_the_panel_rejects_on_a_majority_against(conn: Conn, tmp_path: Path) -> None:
    no = _panel(
        conn, tmp_path, ("fake-b:1", UNFAITHFUL), ("fake-c:1", UNFAITHFUL), ("fake-d:1", FAITHFUL)
    )
    assert no.verdict == "fail"
    assert any(v["issues"] for v in no.evidence["votes"])


def test_a_split_panel_and_a_one_family_panel_conclude_nothing(conn: Conn, tmp_path: Path) -> None:
    split = _panel(conn, tmp_path, ("fake-b:1", FAITHFUL), ("fake-c:1", UNFAITHFUL))
    assert split.verdict == "inconclusive"

    one_family = _panel(conn, tmp_path, ("fake-b:1", FAITHFUL), ("fake-b:2", FAITHFUL))
    assert one_family.verdict == "inconclusive"
    assert "families" in one_family.evidence["reason"]


def test_judges_of_the_extractors_family_are_left_out_of_the_count(
    conn: Conn, tmp_path: Path
) -> None:
    result = _panel(
        conn, tmp_path, ("fake-a:1", FAITHFUL), ("fake-b:1", FAITHFUL), ("fake-c:1", FAITHFUL)
    )

    assert result.verdict == "pass"
    assert result.evidence["skipped"] == ["fake-a:1"]
    assert len(result.evidence["votes"]) == 2


# ---- the whole line, with no one on it ----


def _by_role() -> list[Scripted]:
    """verifiers[0] reads back, verifiers[-1] writes tests, all of them sit on the panel."""

    def reply(prompt: str) -> str:
        if "結構化描述" in prompt and "法規測試設計者" not in prompt and "審查者" not in prompt:
            return json.dumps(SAME_AS_ANNOTATION)
        if "法規測試設計者" in prompt:
            return json.dumps(CASES)
        return json.dumps(FAITHFUL)

    return [Scripted("fake-b:v", reply), Scripted("fake-c:v", reply)]


def _review(conn: Conn, sid: int) -> str:
    return str(_scalar(conn, "SELECT review::text FROM annotation.statement WHERE id = %s", sid))


def test_units_of_lists_the_units_that_have_text(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))

    assert units_of(conn, LAW) == ["art_1", "art_2"]
    assert units_of(conn, "NOPE") == []


def test_from_text_to_conclusion_through_every_gate_without_a_person(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    extractor = Scripted(EXTRACTOR, EXTRACTED)

    result = run(conn, extractor, _by_role(), [LAW], agent_version="1")

    assert (result.units, result.skipped, result.extracted) == (2, 0, 1)
    assert result.dropped == 3  # 兩個單元裡，原文沒有的引文各被丟掉
    assert (result.settled.confirmed, result.settled.rejected) == (1, 0)
    verdicts = evaluate(conn, BEFORE, {"discharging": 1, "compliant": 0}).verdicts
    assert list(verdicts.values()) == ["violation"]
    methods = {
        str(m) for (m,) in conn.execute("SELECT DISTINCT method FROM annotation.verification")
    }
    assert set(ALL_METHODS) <= methods | {"policy"}


def test_a_second_run_does_not_extract_the_same_unit_again(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    extractor = Scripted(EXTRACTOR, EXTRACTED)
    run(conn, extractor, _by_role(), [LAW], agent_version="1")
    calls = len(extractor.prompts)

    again = run(conn, extractor, _by_role(), [LAW], agent_version="1")

    assert again.skipped == 1
    assert len(extractor.prompts) == calls + 1  # 只有沒抽出東西的 art_2 再試一次
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 1


def test_verifiers_of_the_extractors_family_accept_nothing_unless_explicitly_allowed(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    cousins = [Scripted("fake-a:v1", SAME_AS_ANNOTATION), Scripted("fake-a:v2", CASES)]

    strict = run(conn, Scripted(EXTRACTOR, EXTRACTED), cousins, [LAW], agent_version="1")
    assert strict.settled.confirmed == 0
    sid = int(_scalar(conn, "SELECT id FROM annotation.statement"))
    assert _review(conn, sid) == "extracted"  # 同家族的共同偏誤擋不住，所以不接受
    assert evaluate(conn, BEFORE, {"discharging": 1, "compliant": 0}).verdicts == {}


def test_a_failed_model_gate_rejects_even_when_the_deterministic_ones_pass(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    verifiers = [
        Scripted("fake-b:reader", {**SAME_AS_ANNOTATION, "deontic": "permission"}),
        Scripted("fake-c:tester", CASES),
    ]

    result = run(conn, Scripted(EXTRACTOR, EXTRACTED), verifiers, [LAW], agent_version="1")

    assert (result.settled.confirmed, result.settled.rejected) == (0, 1)


# ---- the model adapter ----


def test_build_model_names_the_family_and_refuses_vendors_it_has_no_adapter_for() -> None:
    model = build_model("anthropic:claude-sonnet-5-5")

    assert isinstance(model, AnthropicModel)
    assert (model.name, model.family) == ("anthropic:claude-sonnet-5-5", "anthropic")
    with pytest.raises(ValueError, match="no adapter"):
        build_model("openai:gpt")
    with pytest.raises(ValueError):
        build_model("anthropic")


def test_the_anthropic_adapter_sends_one_user_message_and_joins_the_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    class Messages:
        def create(self, **kwargs: Any) -> Any:
            sent.update(kwargs)
            block = types.SimpleNamespace
            return block(
                content=[
                    block(type="text", text="a"),
                    block(type="tool_use"),
                    block(type="text", text="b"),
                ]
            )

    fake_sdk = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(messages=Messages()))
    monkeypatch.setattr(
        "env_law_ontology.verification.models.importlib",
        types.SimpleNamespace(import_module=lambda name: fake_sdk),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    text = AnthropicModel("claude-sonnet-5-5", max_tokens=100).complete("問題")

    assert text == "ab"
    assert sent == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "問題"}],
    }


def test_the_anthropic_adapter_refuses_to_run_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "env_law_ontology.verification.models.importlib",
        types.SimpleNamespace(import_module=lambda name: types.SimpleNamespace()),
    )

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicModel("claude-sonnet-5-5").complete("x")


# ---- one model under two names ----


def test_two_names_that_answer_every_probe_identically_are_one_model() -> None:
    """託管服務常把同一個模型掛在幾個名字下：名字不同不等於家族不同。"""
    twin = {p: f"答：{i}" for i, p in enumerate(PROBES)}
    a = Scripted("openai:gpt-oss-120b", lambda prompt: twin[prompt])
    b = Scripted("mistral:mistral-small-4", lambda prompt: twin[prompt])

    with pytest.raises(SameModel, match="same model under two names"):
        assert_distinct([a, b])


def test_models_that_answer_differently_are_distinct() -> None:
    a = Scripted("google:gemma", lambda prompt: "甲" + prompt[:3])
    b = Scripted("meta:llama", lambda prompt: "乙" + prompt[:3])

    assert_distinct([a, b])


def test_the_pipeline_can_refuse_to_start_when_two_of_its_models_are_one(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))

    def same(prompt: str) -> str:
        return "同一個答案"

    models = [Scripted("fake-a:x", same), Scripted("fake-b:y", same)]

    with pytest.raises(SameModel):
        run(conn, models[0], models[1:], [LAW], agent_version="1", check_distinct=True)
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0  # 一個單元都沒抽


# ---- penalties that punish a statement in the article they cite ----

PENALTY_TEXT = "違反第一條規定者，處新臺幣一萬元以上十萬元以下罰鍰"


def _penalty_doc() -> dict[str, Any]:
    return {
        "concepts": [],
        "statements": [
            {
                "id": "p1",
                "quote": PENALTY_TEXT,
                "kind": "penalty",
                "sanction": {"effect": "fine", "min": 10000, "max": 100000},
            }
        ],
    }


def _duty_doc(count: int = 1) -> dict[str, Any]:
    duty: dict[str, Any] = cast(Any, EXTRACTED["statements"])[0]
    return {
        "concepts": EXTRACTED["concepts"],
        "statements": [{**duty, "id": f"s{i}"} for i in range(1, count + 1)],
    }


def _extractor_for(duties: int, choose: Callable[[list[int]], object]) -> Scripted:
    def reply(prompt: str) -> str:
        if "候選陳述" in prompt:
            return json.dumps(
                {"punishes": choose([int(i) for i in re.findall(r"^\[(\d+)\]", prompt, re.M)])}
            )
        return json.dumps(
            _duty_doc(duties) if "art_1）" in prompt else _penalty_doc(), ensure_ascii=False
        )

    return Scripted(EXTRACTOR, reply)


def _sanction_target(conn: Conn) -> list[tuple[int, int]]:
    return [
        (int(a), int(b))  # type: ignore[call-overload]
        for a, b in conn.execute("SELECT statement_id, punishes_id FROM annotation.sanction")
    ]


def test_a_penalty_is_linked_to_the_only_statement_of_the_article_it_cites(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, PENALTY_TEXT)))
    model = _extractor_for(1, lambda ids: ids[0])

    result = run(conn, model, [], [LAW], agent_version="1")

    ((penalty, target),) = _sanction_target(conn)
    assert result.linked == 1 and result.extracted == 2 and result.dropped == 0
    assert (
        _scalar(
            conn,
            "SELECT p.path FROM annotation.statement s JOIN corpus.provision p"
            " ON p.id = s.provision_id WHERE s.id = %s",
            target,
        )
        == "art_1"
    )
    conn.execute("UPDATE annotation.statement SET review = 'confirmed' WHERE id = %s", (target,))
    assert ReferencesGate().check(conn, penalty) == GateResult(
        "pass", {"target": "art_1", "cited": ["art_1"], "unresolved": []}
    )
    assert not any("候選陳述" in p for p in model.prompts)  # 只有一個候選，不問模型


def test_a_model_chooses_among_several_candidates(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, PENALTY_TEXT)))
    model = _extractor_for(2, lambda ids: ids[-1])

    run(conn, model, [], [LAW], agent_version="1")

    ((_, target),) = _sanction_target(conn)
    assert target == _scalar(
        conn, "SELECT max(id) FROM annotation.statement WHERE kind = 'prescriptive'"
    )


def test_a_choice_that_is_not_a_candidate_links_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, PENALTY_TEXT)))
    model = _extractor_for(2, lambda ids: 10**6)

    result = run(conn, model, [], [LAW], agent_version="1")

    assert result.linked == 0 and result.dropped == 1
    assert _sanction_target(conn) == []


def test_a_penalty_citing_an_article_with_no_statements_is_dropped(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("與排放無關的條文", PENALTY_TEXT)))

    def reply(prompt: str) -> str:
        return json.dumps(_penalty_doc() if "art_2）" in prompt else {"statements": []})

    result = run(conn, Scripted(EXTRACTOR, reply), [], [LAW], agent_version="1")

    assert result.linked == 0 and result.dropped == 1
    assert _sanction_target(conn) == []


def test_the_panel_sees_the_whole_unit_the_quote_and_the_effect_in_chinese(
    conn: Conn, tmp_path: Path
) -> None:
    """評審看得到整個單元（其餘條件在別的陳述裡），也看得懂罰則效果；往返驗證仍給列舉值。"""
    unit = TEXT + "，處新臺幣一萬元罰鍰"
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(unit, "乙一")))
    doc = _duty_doc()
    doc["statements"].append(
        {
            "id": "p1",
            "quote": "處新臺幣一萬元罰鍰",
            "kind": "penalty",
            "sanction": {"effect": "fine", "min": 10000, "punishes": "s1"},
        }
    )
    out = extract_unit(
        conn, Scripted(EXTRACTOR, doc), LAW, "art_1", agent="fake-model", agent_version="1"
    )
    assert out.loaded is not None
    penalty = out.loaded.ids["p1"]
    judge = Scripted("fake-b:judge", {"faithful": True, "issues": []})

    PanelGate((judge,), min_families=1).check(conn, penalty)

    prompt = judge.prompts[0]
    assert unit in prompt and "標註對應的引文：\n處新臺幣一萬元罰鍰" in prompt
    assert "罰則效果：fine（罰鍰）" in prompt
    assert "罰鍰）" not in render_annotation(conn, penalty)
    assert "罰則效果：fine" in render_annotation(conn, penalty)
