"""違憲失效：被宣告失效的條文版本自失效日起不是推論的依據，結果標明為何缺席。"""

from datetime import date
from pathlib import Path

from envcite_ontology import annotation_read
from envcite_ontology.annotate import load
from envcite_ontology.reasoning import evaluate, evaluate_across
from tests.reasoning_support import Conn, _plain_duty, _scope, _with
from tests.test_annotation import LAW, _doc, _duty, _limit
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_id, _law_v1, _law_v2, _next

RULING_DAY = date(2019, 6, 1)
INVALID_FROM = date(2019, 7, 1)


def _invalidate(conn: Conn, path: str, *, partial: bool = False, no: str = "釋字第 999 號") -> None:
    """一則裁判宣告 `path` 自 INVALID_FROM 失效（fixture 直接寫表；正式資料經 envcite-ruling）。"""
    ruling = int(
        _scalar(
            conn,
            "INSERT INTO corpus.constitutional_ruling"
            " (ruling_no, ruling_kind, decided_on, source_url, excerpt)"
            " VALUES (%s, 'interpretation', %s, 'https://cons.judicial.gov.tw/x', '失其效力')"
            " RETURNING id",
            no,
            RULING_DAY,
        )
    )
    conn.execute(
        "INSERT INTO corpus.ruling_invalidation"
        " (ruling_id, resource_id, provision_id, invalid_from, partial)"
        " SELECT %s, p.resource_id, p.id, %s, %s FROM corpus.provision AS p"
        "  WHERE p.resource_id = %s AND p.path = %s",
        (ruling, INVALID_FROM, partial, _law_id(conn), path),
    )


def _fined(conn: Conn, tmp_path: Path) -> tuple[int, int]:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    penalty = {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [_scope("serious")],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
    }
    loaded = load(conn, _with(_doc(_plain_duty(), penalty), "serious"))
    return loaded.ids["duty"], loaded.ids["fine"]


CASE: dict[str, float] = {"discharging": 1, "cod": 150, "serious": 1, "ap_intent": 1}


def test_an_invalidated_duty_is_no_basis_from_the_day_it_lapses(conn: Conn, tmp_path: Path) -> None:
    """失效日前照常推論；失效日起義務與處罰它的罰則都不列，義務列在 invalidated。"""
    duty, fine = _fined(conn, tmp_path)
    _invalidate(conn, "art_1")

    before = evaluate(conn, date(2019, 6, 30), CASE)
    assert before.verdicts[duty] == "violation"
    assert [p.statement_id for p in before.penalties] == [fine]
    assert before.invalidated == ()

    after = evaluate(conn, INVALID_FROM, CASE)
    assert duty not in after.verdicts
    assert after.penalties == ()
    (gone,) = after.invalidated
    assert (gone.statement_id, gone.ruling_no, gone.invalid_from, gone.partial) == (
        duty,
        "釋字第 999 號",
        INVALID_FROM,
        False,
    )
    assert gone.version_iri.startswith("https://envcite/tw/")


def test_a_partial_invalidation_also_excludes_and_says_partial(conn: Conn, tmp_path: Path) -> None:
    """只宣告一部分：分不出陳述讀的是哪一部分，整個排除，標出 partial 讓人核對。"""
    duty, _ = _fined(conn, tmp_path)
    _invalidate(conn, "art_1", partial=True)

    after = evaluate(conn, INVALID_FROM, CASE)

    assert duty not in after.verdicts
    assert [(i.statement_id, i.partial) for i in after.invalidated] == [(duty, True)]


def test_an_invalidated_penalty_leaves_the_duty(conn: Conn, tmp_path: Path) -> None:
    duty, fine = _fined(conn, tmp_path)
    _invalidate(conn, "art_2")

    after = evaluate(conn, INVALID_FROM, CASE)

    assert after.verdicts[duty] == "violation"
    assert after.penalties == ()
    assert [i.statement_id for i in after.invalidated] == [fine]


def test_list_statements_reports_what_it_leaves_out(conn: Conn, tmp_path: Path) -> None:
    duty, fine = _fined(conn, tmp_path)
    _invalidate(conn, "art_1")

    listed = annotation_read.list_statements(conn, _law_id(conn), INVALID_FROM)

    assert [s.id for s in listed.statements] == [fine]
    assert [(i.statement_id, i.ruling_no, i.invalid_from) for i in listed.invalidated] == [
        (duty, "釋字第 999 號", "2019-07-01")
    ]


def _delegated(conn: Conn, tmp_path: Path) -> tuple[int, int, int]:
    """義務的限值委由第 2 條；2020-03-01 第 2 條修正（100 → 80）。"""
    _first(conn, tmp_path, _law_v1())
    first = load(conn, _doc(_duty(), _limit("乙一", 100)))
    _next(conn, tmp_path, _law_v2())
    second = load(conn, _doc(_limit("乙二", 80)))
    return first.ids["duty"], first.ids["limit100"], second.ids["limit80"]


def test_an_invalidated_limit_is_reported_and_new_text_is_not_affected(
    conn: Conn, tmp_path: Path
) -> None:
    """限值的條文失效：要件未知並標 invalidated_delegation；裁判之後修正的新文字照常解析。"""
    duty, old, new = _delegated(conn, tmp_path)
    _invalidate(conn, "art_2")
    case: dict[str, float] = {"discharging": 1, "cod": 90}

    lapsed = evaluate(conn, date(2019, 8, 1), case)
    assert lapsed.verdicts[duty] == "undetermined"
    condition = next(c for k, c in lapsed.problems if k == "invalidated_delegation")
    assert ("unresolved_delegation", condition) not in lapsed.problems
    assert [i.statement_id for i in lapsed.invalidated] == [old]

    amended = evaluate(conn, date(2020, 6, 1), case)
    assert amended.verdicts[duty] == "violation"  # 90 > 80，新文字不受裁判影響
    assert amended.limit_sources == {condition: new}
    assert amended.invalidated == ()


def test_the_lapse_day_is_a_change_of_law(conn: Conn, tmp_path: Path) -> None:
    """行為在失效日前、裁處在失效日後：兩組陳述各自推論（行政罰法第 5 條），不替呼叫端選。"""
    duty, _, _ = _delegated(conn, tmp_path)
    _invalidate(conn, "art_2")

    moved = evaluate_across(
        conn, date(2019, 6, 15), date(2019, 8, 1), {"discharging": 1, "cod": 150}
    )

    assert moved.at_decision.verdicts[duty] == "undetermined"
    (before,) = moved.earlier
    assert before.as_of == date(2019, 6, 15)
    assert before.result.verdicts[duty] == "violation"
