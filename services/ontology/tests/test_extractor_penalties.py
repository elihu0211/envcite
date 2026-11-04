"""Penalties whose punished statement is not confirmed yet, or is in the article the text cites:
held for linking (`annotation.held_penalty`), or dropped."""

import json
from pathlib import Path
from typing import Any, cast

from envcite_ontology.verification.extract import (
    extract_unit,
)
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import (
    EXTRACTED,
    Conn,
    FakeModel,
    _duty_and_cited_penalty,
)


def _held(conn: Conn) -> list[tuple[list[str], int | None]]:
    """待連結罰則：每份的陳述 id 與指明的受罰陳述。"""
    return [
        ([str(x["id"]) for x in doc["statements"]], cast("int | None", punished))  # type: ignore[index]
        for doc, punished in conn.execute(
            "SELECT document, punished_statement_id FROM annotation.held_penalty ORDER BY id"
        )
    ]


def test_a_penalty_without_a_target_in_the_unit_is_held_for_linking(
    conn: Conn, tmp_path: Path
) -> None:
    """罰則處罰的是原文引用的別條：這個單元寫不進去，留到所有單元抽完再連結，其餘照常寫入。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))

    out = extract_unit(
        conn,
        FakeModel(json.dumps(_duty_and_cited_penalty(), ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and (out.loaded.statements, out.loaded.held) == (1, 1)
    assert out.dropped == ()
    assert _held(conn) == [(["p1"], None)]
    assert _scalar(conn, "SELECT count(*) FROM annotation.sanction") == 0


def test_a_penalty_that_names_a_statement_missing_from_the_unit_is_held_like_one_that_names_none(
    conn: Conn, tmp_path: Path
) -> None:
    """模型不知道別條的陳述 id：指向不存在的 id 與沒有指明是同一回事，留待連結。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = _duty_and_cited_penalty()
    doc["statements"][1]["sanction"]["punishes"] = "elsewhere"

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert out.dropped == ()
    assert _held(conn) == [(["p1"], None)]


def _duty_and_penalty_citing(article: str) -> tuple[str, dict[str, Any]]:
    """A unit that states the duty and a penalty that the model ties to it in the same unit."""
    text = f"排放廢水者應符合標準；違反{article}規定者處罰鍰"
    doc: dict[str, Any] = json.loads(json.dumps(EXTRACTED))
    doc["statements"] = [
        {**doc["statements"][0], "quote": "排放廢水者應符合標準"},
        {
            "id": "p1",
            "quote": f"違反{article}規定者處罰鍰",
            "kind": "sanction",
            "sanction": {"punishes": "s1", "effect": "fine", "min": 1, "max": 2},
        },
    ]
    return text, doc


def test_a_penalty_tied_to_the_unit_but_citing_another_article_is_held_for_linking(
    conn: Conn, tmp_path: Path
) -> None:
    """模型把罰則連到同單元重述義務的陳述；原文引的是第二條：交給連結，不留一個必被引用閘門駁回的連結。"""
    text, doc = _duty_and_penalty_citing("第二條")
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(text, "乙一")))

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert _held(conn) == [(["p1"], None)]


def test_a_penalty_tied_to_the_unit_keeps_its_target_when_the_text_cites_the_unit_itself(
    conn: Conn, tmp_path: Path
) -> None:
    """同單元的受罰陳述還沒確認：罰則存著等它，受罰者已指明。"""
    text, doc = _duty_and_penalty_citing("第一條")
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(text, "乙一")))

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and (out.loaded.statements, out.loaded.held) == (1, 1)
    assert _held(conn) == [(["p1"], out.loaded.ids["s1"])]


def test_an_exception_to_a_held_penalty_is_held_with_it(conn: Conn, tmp_path: Path) -> None:
    """例外要和它所排除的罰則一起寫入，所以跟著罰則一起等連結。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    doc["statements"] = [
        doc["statements"][0],
        {
            "id": "p1",
            "quote": "排放廢水者應符合標準",
            "kind": "sanction",
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

    assert out.loaded is not None and (out.loaded.statements, out.loaded.held) == (1, 2)
    assert out.dropped == ()
    assert _held(conn) == [(["p1", "e1"], None)]


def test_an_empty_sanction_template_on_a_duty_is_ignored(conn: Conn, tmp_path: Path) -> None:
    """真實模型把罰則欄位照樣板填成空值：義務陳述照常寫入，不被當成待連結的罰則。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    doc["statements"][0]["sanction"] = {
        "effect": None,
        "min": None,
        "max": None,
        "repeating": False,
        "punishes": None,
    }

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert _held(conn) == []
    assert _scalar(conn, "SELECT count(*) FROM annotation.sanction") == 0


def test_a_penalty_without_an_effect_is_dropped(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = _duty_and_cited_penalty()
    doc["statements"][1]["sanction"] = {"effect": None, "punishes": None}

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and out.loaded.statements == 1
    assert out.dropped == ("p1: penalty without a sanction effect",)
    assert _held(conn) == []
