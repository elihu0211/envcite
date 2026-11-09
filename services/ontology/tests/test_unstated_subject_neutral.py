"""義務的主體槽位不進推論：主體寫了什麼、或標為「未明示」，罰則鏈的結果都一樣。

推論只讀罰則自己的主體槽位（責任基礎）；被處罰的義務的主體不參與涵攝。所以「未明示視為不受限制」
對候選罰則沒有放大或縮小的效果，和原本把主體寫成被規範的物（`飲用水設備`）或寫成行為人（`事業`）一樣。
"""

from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.annotate import load
from envcite_ontology.reasoning import evaluate
from tests.reasoning_support import Conn, _plain_duty, _scope, _with
from tests.test_annotation import BEFORE, LAW, _doc
from tests.test_incremental import _articles, _first, _law_v1

#: 違反義務的案件事實：排放中，COD 150 超過義務的 100，有故意（行政罰法第 7 條）。
FACTS: dict[str, float] = {"discharging": 1, "cod": 150, "ap_intent": 1}

DUTY_SUBJECTS: dict[str, Any] = {
    "no-subject": None,
    "the-actor": {"literal": "事業"},
    "a-thing-as-subject": {"literal": "飲用水設備"},
    "unstated": {"unstated": True},
}
FINE_SUBJECTS: dict[str, Any] = {
    "unrestricted": None,
    "unstated": {"unstated": True},
    "act-only": {"literal": "污染行為人", "liability": "act"},
}


def _fine(subject: Any) -> dict[str, Any]:
    fine: dict[str, Any] = {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [_scope("discharging")],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
    }
    if subject is not None:
        fine["slots"] = {"subject": subject}
    return fine


@pytest.mark.parametrize("actor", [None, ["act"], ["state"], ["representative"]])
@pytest.mark.parametrize("fine", FINE_SUBJECTS)
@pytest.mark.parametrize("duty", DUTY_SUBJECTS)
def test_the_duty_subject_does_not_change_the_penalty_chain(
    conn: Conn, tmp_path: Path, duty: str, fine: str, actor: list[str] | None
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    statement = _plain_duty()
    if DUTY_SUBJECTS[duty] is not None:
        statement = statement | {"slots": {"subject": DUTY_SUBJECTS[duty]}}
    load(conn, _with(_doc(statement, _fine(FINE_SUBJECTS[fine])), "serious"))
    result = evaluate(conn, BEFORE, FACTS, actor_bases=actor)

    assert "violation" in result.verdicts.values()
    listed = [p for p in result.penalties if not p.conditional]
    if fine != "act-only" or actor == ["act"]:
        assert len(listed) == 1 and result.subject_mismatch == ()
    elif actor is None:
        assert len(result.penalties) == 1 and result.penalties[0].conditional
    else:
        assert result.penalties == () and len(result.subject_mismatch) == 1
