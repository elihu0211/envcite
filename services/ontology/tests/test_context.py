"""An item (款) is half a sentence: its subject and its penalty are written in the paragraph (項)
above it. Extractor and judges must see that paragraph, while every anchor stays on the item's
own text. The case is the one the held-out law exposed: the fine in the paragraph, the offence
in the item."""

from pathlib import Path

import psycopg
import pytest

from envcite_ontology.annotate import Loaded, load
from envcite_ontology.verification.context import ancestors_text
from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.gates import NumbersGate
from envcite_ontology.verification.model_gates import PanelGate
from tests.model_gates_support import EXTRACTOR, Scripted
from tests.test_annotation import LAW, _doc, _duty
from tests.test_incremental import _articles, _first, _law_v1

Conn = psycopg.Connection[tuple[object, ...]]

CHAPEAU = (
    "公私場所有下列情形之一者，處新臺幣二萬元以上一百萬元以下罰鍰；"
    "其違反者為工商廠、場，處新臺幣十萬元以上二千萬元以下罰鍰："
)
ITEM = "一、違反第一條規定。"
ARTICLE_2 = f"{CHAPEAU}\r\n{ITEM}"
ITEM_PATH = "art_2/para_1/pt_1"


def _fine(min_: int, max_: int, amount_text: str) -> dict[str, object]:
    return {
        "id": "fine",
        "law": LAW,
        "path": ITEM_PATH,
        "text": ITEM,
        "kind": "sanction",
        "sanction": {
            "punishes": "duty",
            "effect": "fine",
            "min": min_,
            "max": max_,
            "amount_text": amount_text,
        },
    }


def _loaded(conn: Conn, tmp_path: Path, *fine: dict[str, object]) -> Loaded:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", ARTICLE_2)))
    return load(conn, _doc(_duty(), *fine))


def _provision_id(conn: Conn, path: str) -> int:
    row = conn.execute("SELECT id FROM corpus.provision WHERE path = %s", (path,)).fetchone()
    assert row is not None
    return int(str(row[0]))


def test_an_item_sees_the_paragraph_above_it_and_nothing_else_does(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", ARTICLE_2)))

    assert ancestors_text(conn, _provision_id(conn, ITEM_PATH)) == CHAPEAU
    assert ancestors_text(conn, _provision_id(conn, "art_2/para_1")) == ""
    assert ancestors_text(conn, _provision_id(conn, "art_1")) == ""


def test_the_extractor_is_shown_the_paragraph_of_an_item_but_quotes_the_item(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", ARTICLE_2)))
    model = Scripted(EXTRACTOR, {"concepts": [], "statements": []})

    extract_unit(conn, model, LAW, ITEM_PATH, agent_version="1")
    extract_unit(conn, model, LAW, "art_2/para_1", agent_version="1")

    item_prompt, paragraph_prompt = model.prompts
    assert "上層條文" in item_prompt and CHAPEAU in item_prompt
    assert item_prompt.rstrip().endswith(ITEM)  # 原文是款自己
    assert "上層條文：" not in paragraph_prompt  # 頂層單元沒有上層


def test_the_judges_see_the_paragraph_above_the_item(conn: Conn, tmp_path: Path) -> None:
    loaded = _loaded(conn, tmp_path, _fine(100000, 20000000, "新臺幣十萬元以上二千萬元以下罰鍰"))
    judges = tuple(
        Scripted(name, {"faithful": True, "issues": []})
        for name in ("fake-b:one", "fake-c:two", "fake-d:three")
    )

    PanelGate(judges).check(conn, loaded.ids["fine"])

    assert all(CHAPEAU in j.prompts[0] for j in judges)


@pytest.mark.parametrize(
    ("low", "high", "amount_text", "verdict"),
    [
        pytest.param(20000, 1000000, "新臺幣二萬元以上一百萬元以下罰鍰", "pass", id="first-tier"),
        pytest.param(
            100000, 20000000, "新臺幣十萬元以上二千萬元以下罰鍰", "pass", id="second-tier"
        ),
        pytest.param(20000, 20000000, "新臺幣二萬元以上一百萬元以下罰鍰", "fail", id="mixed-tiers"),
        pytest.param(100000, 20000000, "新臺幣一萬元罰鍰", "fail", id="amount-not-in-text"),
    ],
)
def test_the_amounts_of_an_item_are_checked_against_the_paragraph_above_it(
    conn: Conn, tmp_path: Path, low: int, high: int, amount_text: str, verdict: str
) -> None:
    loaded = _loaded(conn, tmp_path, _fine(low, high, amount_text))

    result = NumbersGate().check(conn, loaded.ids["fine"])

    assert result is not None and result.verdict == verdict
