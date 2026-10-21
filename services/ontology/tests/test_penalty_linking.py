"""Penalties that punish a statement in the article they cite: linked, chosen among candidates,
or dropped."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from envcite_ontology.verification import (
    PanelGate,
)
from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.gates import GateResult, ReferencesGate
from envcite_ontology.verification.model_gates import render_annotation
from envcite_ontology.verification.run import (
    run,
)
from tests.model_gates_support import (
    EXTRACTOR,
    TEXT,
    Conn,
    Scripted,
)
from tests.test_annotation import LAW
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
        for a, b in conn.execute(
            "SELECT statement_id, punished_statement_id FROM annotation.sanction"
        )
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
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed' WHERE id = %s", (target,)
    )
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
        conn, "SELECT max(id) FROM annotation.statement WHERE statement_kind = 'prescriptive'"
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
            "kind": "sanction",
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
