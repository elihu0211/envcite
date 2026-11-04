"""Penalties are written only once the duty they punish is confirmed (migration 0055): held until
then, linked to every confirmed duty of the provisions they cite, or left with a queryable state."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest

from envcite_ontology import violation
from envcite_ontology.annotate import load
from envcite_ontology.verification import (
    PanelGate,
)
from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.gates import GateResult, ReferencesGate
from envcite_ontology.verification.link import link_penalties, penalized
from envcite_ontology.verification.model_gates import render_annotation
from envcite_ontology.verification.pipeline import settle
from envcite_ontology.verification.run import (
    run,
)
from tests.model_gates_support import (
    EXTRACTOR,
    TEXT,
    Conn,
    Scripted,
)
from tests.test_annotation import LAW, _doc
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import EXTRACTED

PENALTY_TEXT = "違反第一條規定者，處新臺幣一萬元以上十萬元以下罰鍰"


def _penalty_doc() -> dict[str, Any]:
    return {
        "concepts": [],
        "statements": [
            {
                "id": "p1",
                "quote": PENALTY_TEXT,
                "kind": "sanction",
                "sanction": {"effect": "fine", "min": 10000, "max": 100000},
            }
        ],
    }


def _duty_doc(count: int = 1) -> dict[str, Any]:
    duty: dict[str, Any] = cast(Any, EXTRACTED["statements"])[0]
    return {
        "concepts": EXTRACTED["concepts"],
        # 內容要不同：相同的陳述載入時會併成一個，就沒有候選可選
        "statements": [
            {**duty, "id": f"s{i}", "slots": {"object": {"literal": f"第{i}類"}}}
            for i in range(1, count + 1)
        ],
    }


def _corpus(conn: Conn, tmp_path: Path, *texts: str) -> None:
    """匯入法規並寫處罰依賴圖（`envcite-sync` 在匯入後做的）。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(*texts)))
    violation.extract(conn)
    conn.commit()


def _draft(statements: list[dict[str, Any]]) -> dict[str, Any]:
    """未審核的草稿：抽取者的概念加上錨定好的陳述。"""
    return {
        **_doc(review="extracted", method="llm"),
        "concepts": EXTRACTED["concepts"],
        "statements": statements,
    }


def _with_text(
    statements: list[dict[str, Any]], path: str = "art_1", text: str = TEXT
) -> list[dict[str, Any]]:
    """抽取者的陳述加上錨定（法規、單元、原文），成為可載入的文件。"""
    return [
        {**{k: v for k, v in s.items() if k != "quote"}, "law": LAW, "path": path, "text": text}
        for s in statements
    ]


def _extractor_for(duties: int, choose: Callable[[list[int]], object]) -> Scripted:
    def reply(prompt: str) -> str:
        if "候選陳述" in prompt:
            return json.dumps(
                {"punishes": choose([int(i) for i in re.findall(r"^\[(\d+)\]", prompt, re.M)])}
            )
        return json.dumps(
            _duty_doc(duties) if f"{LAW} art_1）" in prompt else _penalty_doc(), ensure_ascii=False
        )

    return Scripted(EXTRACTOR, reply)


@dataclass(frozen=True)
class _ConfirmsDuties:
    """替身閘門：義務通過；罰則不適用（由引用閘門定）。"""

    method: str = "fake"
    verifier: str = "gate:fake@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        kind = _scalar(
            conn,
            "SELECT statement_kind::text FROM annotation.statement WHERE id = %s",
            statement_id,
        )
        return GateResult("pass") if kind == "prescriptive" else None


def _run(conn: Conn, model: Scripted) -> Any:
    return run(
        conn,
        model,
        [],
        [LAW],
        agent_version="1",
        required=["fake", "references"],
        gates=(_ConfirmsDuties(), ReferencesGate()),
    )


def _states(conn: Conn) -> list[tuple[str, int]]:
    return cast(
        list[tuple[str, int]],
        conn.execute(
            "SELECT state, count(*) FROM annotation.held_penalty_state GROUP BY 1 ORDER BY 1"
        ).fetchall(),
    )


def _sanction_target(conn: Conn) -> list[tuple[int, int]]:
    return [
        (int(a), int(b))  # type: ignore[call-overload]
        for a, b in conn.execute(
            "SELECT statement_id, punished_statement_id FROM annotation.sanction"
        )
    ]


def test_a_penalty_is_linked_to_the_only_statement_of_the_article_it_cites(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)
    model = _extractor_for(1, lambda ids: ids[0])

    result = _run(conn, model)

    ((penalty, target),) = _sanction_target(conn)
    assert result.linked == 1 and result.extracted == 2 and result.dropped == 0
    assert _states(conn) == [("linked", 1)]
    assert (
        _scalar(
            conn,
            "SELECT p.path FROM annotation.statement s JOIN corpus.provision p"
            " ON p.id = s.provision_id WHERE s.id = %s",
            target,
        )
        == "art_1"
    )
    assert ReferencesGate().check(conn, penalty) == GateResult(
        "pass", {"target": "art_1", "cited": ["art_1"], "unresolved": []}
    )
    assert not any("候選陳述" in p for p in model.prompts)  # 只有一個候選，不問模型


def test_a_model_chooses_among_several_candidates(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)
    model = _extractor_for(2, lambda ids: ids[-1])

    _run(conn, model)

    ((_, target),) = _sanction_target(conn)
    assert target == _scalar(
        conn, "SELECT max(id) FROM annotation.statement WHERE statement_kind = 'prescriptive'"
    )


@dataclass(frozen=True)
class _ConfirmsLatestDuty:
    """替身閘門：最後抽到的義務通過、其餘義務失敗；罰則不適用（由引用閘門定）。"""

    method: str = "fake"
    verifier: str = "gate:fake@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        latest = _scalar(
            conn, "SELECT max(id) FROM annotation.statement WHERE statement_kind = 'prescriptive'"
        )
        kind = _scalar(
            conn,
            "SELECT statement_kind::text FROM annotation.statement WHERE id = %s",
            statement_id,
        )
        if kind != "prescriptive":
            return None
        return GateResult("pass" if statement_id == latest else "fail")


def test_a_penalty_is_linked_only_to_a_confirmed_duty(conn: Conn, tmp_path: Path) -> None:
    """被引條文的兩個義務一個確認、一個駁回：罰則只連得到確認的那個，不必問模型。"""
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)
    model = _extractor_for(2, lambda ids: ids[0])  # 兩個都給它挑的話，會挑到被駁回的

    result = run(
        conn,
        model,
        [],
        [LAW],
        agent_version="1",
        required=["fake", "references"],
        gates=(_ConfirmsLatestDuty(), ReferencesGate()),
    )

    ((penalty, target),) = _sanction_target(conn)
    assert target == _scalar(
        conn, "SELECT max(id) FROM annotation.statement WHERE statement_kind = 'prescriptive'"
    )
    assert not any("候選陳述" in p for p in model.prompts)  # 只剩一個已確認的候選
    assert (
        _scalar(conn, "SELECT confirm_state::text FROM annotation.assertion WHERE id = %s", penalty)
        == "confirmed"
    )
    assert (result.settled.confirmed, result.settled.rejected) == (2, 1)


def test_a_penalty_citing_two_articles_is_written_once_for_each_duty_it_punishes(
    conn: Conn, tmp_path: Path
) -> None:
    """「違反第一條或第二條規定者」處罰兩個義務：一個罰則陳述只處罰一個，所以各寫一份。"""
    both = "違反第一條或第二條規定者，處新臺幣一萬元以上十萬元以下罰鍰"
    _corpus(conn, tmp_path, TEXT, TEXT, both)
    doc = _penalty_doc()
    doc["statements"][0]["quote"] = both

    def reply(prompt: str) -> str:
        if "候選陳述" in prompt:
            return json.dumps({"punishes": re.findall(r"^\[(\d+)\]", prompt, re.M)})
        return json.dumps(
            _duty_doc() if f"{LAW} art_3）" not in prompt else doc, ensure_ascii=False
        )

    result = _run(conn, Scripted(EXTRACTOR, reply))

    duties = [
        int(r[0])  # type: ignore[call-overload]
        for r in conn.execute(
            "SELECT id FROM annotation.statement WHERE statement_kind = 'prescriptive' ORDER BY id"
        )
    ]
    assert len(duties) == 2
    assert sorted(t for _, t in _sanction_target(conn)) == duties
    assert result.linked == 2 and result.dropped == 0


def test_a_unit_a_penalty_cites_is_told_it_carries_a_duty(conn: Conn, tmp_path: Path) -> None:
    """處罰條文以「違反」引用的單元，抽取時提示必有義務；抽不出義務就送回修復。"""
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)
    assert penalized(conn, LAW) == {"art_1": ["art_2"]}
    permission = {
        "concepts": [],
        "statements": [
            {"id": "s1", "quote": TEXT, "kind": "prescriptive", "deontic": "permission"}
        ],
    }
    model = Scripted(EXTRACTOR, permission)

    extract_unit(conn, model, LAW, "art_1", agent_version="1", punished_by=["art_2"])

    assert "被同法的處罰條文（art_2）以「違反」引用" in model.prompts[0]
    assert any("卻沒有任何 obligation 或 prohibition" in p for p in model.prompts[1:])


def test_a_choice_that_is_not_a_candidate_links_nothing(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)
    model = _extractor_for(2, lambda ids: 10**6)

    result = _run(conn, model)

    assert result.linked == 0 and result.dropped == 1
    assert _sanction_target(conn) == []
    assert _states(conn) == [("linkable", 1)]  # 下次再挑


def test_a_penalty_citing_an_article_with_no_duty_waits_and_the_gap_is_listed(
    conn: Conn, tmp_path: Path
) -> None:
    """被引條文抽不出義務：罰則留著（no_duty），被引條文列在 duty_gap 待重新抽取。"""
    _corpus(conn, tmp_path, "與排放無關的條文", PENALTY_TEXT)

    def reply(prompt: str) -> str:
        return json.dumps(_penalty_doc() if f"{LAW} art_2）" in prompt else {"statements": []})

    result = _run(conn, Scripted(EXTRACTOR, reply))

    assert result.linked == 0 and result.dropped == 0
    assert _sanction_target(conn) == []
    assert _states(conn) == [("no_duty", 1)]
    assert conn.execute("SELECT path FROM annotation.duty_gap").fetchall() == [("art_1",)]


def test_a_penalty_whose_duty_is_still_undecided_waits(conn: Conn, tmp_path: Path) -> None:
    """義務還沒有定論（沒有閘門能判）：罰則不寫成陳述，也不駁回，狀態是 awaiting_duty。"""
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)

    result = run(conn, _extractor_for(1, lambda ids: ids[0]), [], [LAW], agent_version="1")

    assert result.linked == 0 and result.settled.rejected == 0
    assert _states(conn) == [("awaiting_duty", 1)]


def test_the_schema_refuses_a_penalty_of_a_duty_that_is_not_confirmed(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)
    duty = load(conn, _draft(_with_text(_duty_doc()["statements"]))).ids["s1"]
    penalty = _with_text(_penalty_doc()["statements"], "art_2", PENALTY_TEXT)
    penalty[0]["sanction"]["punishes_id"] = duty

    loaded = load(conn, _draft(penalty))  # 載入改存成待連結罰則
    assert (loaded.statements, loaded.held) == (0, 1)
    # 繞過載入、直接寫一個罰則列：綱要擋下
    sid = _scalar(
        conn,
        "INSERT INTO annotation.assertion (assertion_kind, derivation_id)"
        " SELECT 'statement', derivation_id FROM annotation.assertion WHERE id = %s RETURNING id",
        duty,
    )
    conn.execute(
        "INSERT INTO annotation.statement (id, provision_id, content_sha256, statement_kind)"
        " SELECT %s, provision_id, content_sha256, 'sanction' FROM annotation.held_penalty",
        (sid,),
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="is not confirmed"):
        conn.execute(
            "INSERT INTO annotation.sanction (statement_id, punished_statement_id, legal_effect)"
            " VALUES (%s, %s, 'fine')",
            (sid, duty),
        )
    conn.rollback()


def test_a_confirmed_penalty_of_a_duty_rejected_later_is_not_settled_again(
    conn: Conn, tmp_path: Path
) -> None:
    """罰則寫入後義務被駁回：未接受的罰則隨之駁回，不會被接受。"""
    _corpus(conn, tmp_path, TEXT, PENALTY_TEXT)
    duty = load(conn, _draft(_with_text(_duty_doc()["statements"]))).ids["s1"]
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed' WHERE id = %s", (duty,)
    )
    penalty = _with_text(_penalty_doc()["statements"], "art_2", PENALTY_TEXT)
    penalty[0]["sanction"]["punishes_id"] = duty
    p1 = load(conn, _draft(penalty)).ids["p1"]
    conn.execute("SELECT annotation.set_confirm_state(%s, 'rejected', 'wrong reading')", (duty,))
    conn.commit()

    settled = settle(conn, ["references"], (ReferencesGate(),))

    assert settled.rejected == 1
    assert _scalar(
        conn, "SELECT confirm_state::text FROM annotation.assertion WHERE id = %s", p1
    ) == ("rejected")


def test_the_panel_sees_the_whole_unit_the_quote_and_the_effect_in_chinese(
    conn: Conn, tmp_path: Path
) -> None:
    """評審看得到整個單元（其餘條件在別的陳述裡），也看得懂罰則效果；往返驗證仍給列舉值。"""
    unit = TEXT + "，處新臺幣一萬元罰鍰"
    _corpus(conn, tmp_path, unit, "乙一")
    doc = _duty_doc()
    doc["statements"].append(
        {
            "id": "p1",
            "quote": "處新臺幣一萬元罰鍰",
            "kind": "sanction",
            "sanction": {"effect": "fine", "min": 10000, "punishes": "s1"},
        }
    )
    out = extract_unit(
        conn, Scripted(EXTRACTOR, doc), LAW, "art_1", agent="fake-model", agent_version="1"
    )
    assert out.loaded is not None
    duty = out.loaded.ids["s1"]
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed' WHERE id = %s", (duty,)
    )
    penalty = link_penalties(conn, None).ids[f"p1@{duty}"]
    judge = Scripted("fake-b:judge", {"faithful": True, "issues": []})

    PanelGate((judge,), min_families=1).check(conn, penalty)

    prompt = judge.prompts[0]
    assert unit in prompt and "標註對應的引文：\n處新臺幣一萬元罰鍰" in prompt
    assert "罰則效果：fine（罰鍰）" in prompt
    assert "罰鍰）" not in render_annotation(conn, penalty)
    assert "罰則效果：fine" in render_annotation(conn, penalty)
