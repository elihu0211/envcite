"""Incremental import (03-04 §4): what a second snapshot does to what the first wrote."""

import hashlib
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from env_law_ontology.acquire.moj import Moj, content_sha256, pcode
from env_law_ontology.ingest import audit, importer
from env_law_ontology.ingest.importer import Outcome
from tests.test_import import _law, _scalar, _snapshot

Conn = psycopg.Connection[tuple[object, ...]]


@pytest.fixture(autouse=True)
def _no_batch_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """One law is a batch of one: any ratio over it is 0 or 100%, which says nothing."""
    monkeypatch.setattr(audit, "LIMITS", {k: (d, 1.0) for k, (d, _) in audit.LIMITS.items()})


V1 = (
    "1.中華民國八十年五月二十三日總統令制定公布全文 3 條\r\n"
    "2.中華民國一百零七年八月一日總統令修正公布全文 3 條；並自公布日施行"
)
AMEND = (
    "\r\n3.中華民國一百零九年三月一日總統令修正公布第 2 條條文；並增訂第 3 條條文；並自公布日施行"
)


def _articles(*texts: str) -> list[dict[str, str]]:
    return [
        {"ArticleType": "A", "ArticleNo": f"第 {n} 條", "ArticleContent": t}
        for n, t in enumerate(texts, 1)
    ]


def _law_v1(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"LawHistories": V1, "LawArticles": _articles("甲一", "乙一")}
    return _law("T0000001", "噪音管制法", **(base | over))


def _law_v2(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "LawHistories": V1 + AMEND,
        "LawModifiedDate": "20200301",
        "LawArticles": _articles("甲一", "乙二", "丙二"),
    }
    return _law("T0000001", "噪音管制法", **(base | over))


def _next(conn: Conn, root: Path, law: dict[str, Any]) -> Outcome:
    """The next full snapshot, as the acquisition step would leave it; import state is kept."""
    name = hashlib.sha256(json.dumps(law).encode()).hexdigest()[:16] + ".zip"
    with zipfile.ZipFile(root / name, "w") as z:
        z.writestr("data.json", json.dumps({"UpdateDate": "2026/9/19", "Laws": [law]}))
    row = conn.execute(
        "INSERT INTO source.snapshot (feed, data_date, sha256, size, location, attribution)"
        " VALUES ('moj_law', '2026-09-19', %s, 1, %s, 'x') RETURNING id",
        (hashlib.sha256(name.encode()).digest(), name),
    ).fetchone()
    assert row is not None
    conn.execute(
        "UPDATE source.item SET content_sha256 = %s, last_snapshot_id = %s WHERE key = %s",
        (content_sha256(law), row[0], pcode(law)),
    )
    conn.commit()
    return importer.run(conn, root, Moj())


def _first(conn: Conn, root: Path, law: dict[str, Any]) -> None:
    _snapshot(conn, root, [law])
    assert importer.run(conn, root, Moj()).actions == {"added": 1}


def _law_id(conn: Conn) -> int:
    return int(_scalar(conn, "SELECT id FROM corpus.legal_resource WHERE local_id = 'T0000001'"))


def _at(conn: Conn, on: date) -> dict[str, tuple[str, str | None]]:
    return {
        str(path): (str(status), text)  # type: ignore[misc]
        for path, status, text in conn.execute(
            "SELECT s.path, s.status, corpus.article_text(s.version_id)"
            "  FROM corpus.provisions_at(%s, 'zho', %s) AS s",
            (_law_id(conn), on),
        )
    }


def test_an_amendment_adds_a_version_and_closes_the_previous_one(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    assert _next(conn, tmp_path, _law_v2()).actions == {"versioned": 1}

    assert conn.execute(
        "SELECT version_date, text_period FROM corpus.legal_expression"
        " WHERE resource_id = %s ORDER BY version_date",
        (_law_id(conn),),
    ).fetchall() == [
        (date(2018, 8, 1), psycopg.types.range.Range(date(2018, 8, 1), date(2020, 3, 1), "[)")),
        (date(2020, 3, 1), psycopg.types.range.Range(date(2020, 3, 1), None, "[)")),
    ]
    # 各單元各自銜接：沒有空隙，也不重疊。
    assert _at(conn, date(2019, 1, 1)) == {
        "art_1": ("in_force", "甲一"),
        "art_2": ("in_force", "乙一"),
    }
    # 修正公布之後、生效之前：修正的條仍是舊文，新增的條尚未施行，其餘已是新版。
    assert _at(conn, date(2020, 3, 2)) == {
        "art_1": ("in_force", "甲一"),
        "art_2": ("in_force", "乙一"),
        "art_3": ("not_yet_in_force", None),
    }
    assert _at(conn, date(2021, 1, 1)) == {
        "art_1": ("in_force", "甲一"),
        "art_2": ("in_force", "乙二"),
        "art_3": ("in_force", "丙二"),
    }
    # 每一筆公布事件連到版本日相同的版本，不只最新的一個。
    assert conn.execute(
        "SELECT event_date FROM corpus.lifecycle_event"
        " WHERE resource_id = %s AND expression_id IS NOT NULL ORDER BY event_date",
        (_law_id(conn),),
    ).fetchall() == [(date(2018, 8, 1),), (date(2020, 3, 1),)]


def test_a_correction_adds_rows_and_ends_the_old_ones(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    corrected = _law_v1(LawArticles=_articles("甲一", "乙一（更正）"))
    assert _next(conn, tmp_path, corrected).actions == {"corrected": 1}

    assert _at(conn, date(2020, 1, 1))["art_2"] == ("in_force", "乙一（更正）")
    assert (
        _scalar(
            conn, "SELECT count(*) FROM corpus.provision_version WHERE superseded_at IS NOT NULL"
        )
        == 1
    )
    assert conn.execute(
        "SELECT code, detail FROM source.import_problem WHERE code = 'corrected'"
    ).fetchall() == [("corrected", "1 個單元的文字改變")]
    # 過去的認知仍在：被取代的列保有舊文。
    assert conn.execute(
        "SELECT content FROM corpus.provision_version"
        " WHERE superseded_at IS NOT NULL AND resource_id = %s",
        (_law_id(conn),),
    ).fetchall() == [("乙一",)]


def test_a_newly_repealed_law_only_gets_an_end_date(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    repeal = "\r\n3.中華民國一百十年一月十日總統令公布廢止"
    repealed = _law_v1(LawHistories=V1 + repeal, LawAbandonNote="廢", LawModifiedDate="20210110")
    assert _next(conn, tmp_path, repealed).actions == {"refreshed": 1}

    assert _at(conn, date(2020, 1, 1))["art_1"][0] == "in_force"
    assert _at(conn, date(2021, 1, 12))["art_1"][0] == "no_longer_in_force"
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM corpus.legal_expression WHERE resource_id = %s",
            _law_id(conn),
        )
        == 1
    )


def test_a_version_older_than_the_latest_is_refused_and_leaves_the_corpus_alone(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    _next(conn, tmp_path, _law_v2())
    older = _law_v1(LawHistories=V1.replace("一百零七年八月一日", "一百零六年八月一日"))
    assert _next(conn, tmp_path, older).actions == {"regressed": 1}

    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM corpus.legal_expression WHERE resource_id = %s",
            _law_id(conn),
        )
        == 2
    )
    assert conn.execute(
        "SELECT code FROM source.import_problem WHERE level = 'error'"
    ).fetchall() == [("version_regressed",)]


def test_a_commencement_resolved_later_closes_the_open_previous_version(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    # 新版的施行說明列出第 2 條：起點未知，前一版的第 2 條維持開放。
    note = "第 2 條自一百十年一月一日施行。"
    assert _next(conn, tmp_path, _law_v2(LawEffectiveNote=note)).actions == {"versioned": 1}
    assert _at(conn, date(2021, 6, 1))["art_2"] == ("in_force", "乙一")

    # 之後說明消失、起點確定：只更新推導，前一版在同一次寫入中被結束。
    assert _next(conn, tmp_path, _law_v2()).actions == {"refreshed": 1}
    assert _at(conn, date(2021, 6, 1))["art_2"] == ("in_force", "乙二")
    assert _at(conn, date(2020, 3, 2))["art_2"] == ("in_force", "乙一")


@pytest.mark.parametrize("mutation", [{"LawEnglish": "x"}])
def test_a_change_in_fields_the_corpus_does_not_use_changes_nothing(
    conn: Conn, tmp_path: Path, mutation: dict[str, str]
) -> None:
    _first(conn, tmp_path, _law_v1())
    assert _next(conn, tmp_path, _law_v1(**mutation)).actions == {"unchanged": 1}
    assert (
        _scalar(
            conn, "SELECT count(*) FROM corpus.provision_version WHERE superseded_at IS NOT NULL"
        )
        == 0
    )
