"""違憲失效（A5）：裁判由人載入，失效日依查詢日解析；裁判之後修正的新文字不受影響。"""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_ontology.ruling import RulingError, load
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _first, _law_v1, _law_v2, _next

Conn = psycopg.Connection[tuple[object, ...]]

URL = "https://cons.judicial.gov.tw/docdata.aspx?fid=100&id=1"


def _ruling(no: str = "釋字第1號", decided: str = "2019-01-01", **over: Any) -> dict[str, Any]:
    return {
        "ruling_no": no,
        "kind": "interpretation",
        "decided_on": decided,
        "source_url": URL,
        "excerpt": "甲法第一條自本解釋公布之日起，至遲於屆滿一年時失其效力。",
        "invalidates": [{"law": LAW, "path": "art_1", "invalid_from": "2020-01-01"}],
        **over,
    }


def _invalid(conn: Conn, on: date) -> list[tuple[str, str]]:
    resource = _scalar(conn, "SELECT id FROM corpus.legal_resource WHERE local_id = %s", LAW)
    return [
        (str(no), str(path))
        for no, path in conn.execute(
            "SELECT ruling_no, path FROM corpus.invalidations_at(%s, 'zho', %s)", (resource, on)
        )
    ]


def test_a_ruling_invalidates_from_its_date_and_not_before(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, {"rulings": [_ruling()]})

    assert (loaded.rulings, loaded.invalidations, loaded.repeated) == (1, 1, 0)
    assert _invalid(conn, date(2019, 12, 31)) == []
    assert _invalid(conn, date(2020, 1, 1)) == [("釋字第1號", "art_1")]


def test_loading_the_same_ruling_again_changes_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, {"rulings": [_ruling()]})

    again = load(conn, {"rulings": [_ruling()]})

    assert (again.repeated, again.invalidations) == (1, 0)
    assert _scalar(conn, "SELECT count(*) FROM corpus.ruling_invalidation") == 1
    with pytest.raises(RulingError, match="內容與這次不同"):
        load(conn, {"rulings": [_ruling(excerpt="改過")]})


def test_a_bad_document_is_rejected_whole(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    missing = {"law": LAW, "path": "art_9", "invalid_from": "2020-01-01"}
    early = {"law": LAW, "path": "art_1", "invalid_from": "2018-12-31"}
    with pytest.raises(RulingError, match="art_9"):
        load(conn, {"rulings": [_ruling("釋字第2號"), _ruling("釋字第3號", invalidates=[missing])]})
    with pytest.raises(RulingError, match="precedes the ruling date"):
        load(conn, {"rulings": [_ruling(invalidates=[early])]})
    assert _scalar(conn, "SELECT count(*) FROM corpus.constitutional_ruling") == 0


def test_text_amended_after_the_ruling_is_not_invalidated(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    _next(conn, tmp_path, _law_v2())
    amended = {"law": LAW, "path": "art_2", "invalid_from": "2020-01-01"}
    load(conn, {"rulings": [_ruling(invalidates=[amended])]})

    assert _invalid(conn, date(2020, 3, 2)) == [("釋字第1號", "art_2")]
    assert _invalid(conn, date(2020, 3, 5)) == []


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"kind": "decree"}, id="unknown-kind"),
        pytest.param({"decided_on": "not-a-date"}, id="bad-date"),
        pytest.param({"invalidates": [{"law": LAW, "path": "art_1"}]}, id="missing-invalid-from"),
        pytest.param({"excerpt": None}, id="null-excerpt"),
    ],
)
def test_a_document_of_the_wrong_shape_is_rejected_before_anything_is_written(
    conn: Conn, tmp_path: Path, bad: dict[str, Any]
) -> None:
    _first(conn, tmp_path, _law_v1())
    with pytest.raises(RulingError, match="文件欄位缺漏或型別不合"):
        load(conn, {"rulings": [_ruling(**bad)]})
    assert _scalar(conn, "SELECT count(*) FROM corpus.constitutional_ruling") == 0


def test_a_document_without_rulings_is_rejected(conn: Conn) -> None:
    with pytest.raises(RulingError, match="文件欄位缺漏或型別不合"):
        load(conn, {})
