import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from env_law_ontology.acquire.moj import Moj, content_sha256, pcode
from env_law_ontology.ingest import importer
from env_law_ontology.ingest.law import PARSER_VERSION

Conn = psycopg.Connection[tuple[object, ...]]

HISTORY = (
    "1.中華民國八十年五月二十三日總統令制定公布全文 3 條\r\n"
    "2.中華民國一百零七年八月一日總統華總一義字第 10700080891 號令修正公布全文 3 條；並自公布日施行"
)


def _law(code: str, name: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "LawLevel": "法律",
        "LawName": name,
        "LawURL": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={code}",
        "LawModifiedDate": "20180801",
        "LawEffectiveDate": "",
        "LawEffectiveNote": "",
        "LawAbandonNote": "",
        "LawHistories": HISTORY,
        "LawArticles": [
            {
                "ArticleType": "A",
                "ArticleNo": "第 1 條",
                "ArticleContent": "為防制噪音，特制定本法。",
            },
            {
                "ArticleType": "A",
                "ArticleNo": "第 2 條",
                "ArticleContent": "本法用詞如下：\r\n一、噪音。\r\n二、振動。",
            },
        ],
    }
    return base | over


def _snapshot(conn: Conn, root: Path, laws: list[dict[str, Any]], *, feed: str = "moj_law") -> None:
    """A stored snapshot the way the acquisition step leaves it."""
    name = f"{feed}.zip"
    with zipfile.ZipFile(root / name, "w") as z:
        z.writestr("data.json", json.dumps({"UpdateDate": "2026/9/18 上午 12:00:00", "Laws": laws}))
    conn.execute("DELETE FROM source.import_problem")
    conn.execute("DELETE FROM source.item WHERE feed = %s", (feed,))
    conn.execute("DELETE FROM source.snapshot WHERE feed = %s", (feed,))
    row = conn.execute(
        "INSERT INTO source.snapshot (feed, data_date, sha256, size, location, attribution)"
        " VALUES (%s, '2026-09-18', %s, 1, %s, 'x') RETURNING id",
        (feed, bytes(32), name),
    ).fetchone()
    assert row is not None
    for law in laws:
        conn.execute(
            "INSERT INTO source.item"
            " (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
            " VALUES (%s, %s, %s, %s, %s)",
            (feed, pcode(law), content_sha256(law), row[0], row[0]),
        )
    conn.commit()


def _scalar(conn: Conn, sql: str, *args: object) -> Any:
    row = conn.execute(sql, args or None).fetchone()
    assert row is not None
    return row[0]


def test_a_law_is_written_with_its_version_events_and_provisions(
    conn: Conn, tmp_path: Path
) -> None:
    _snapshot(conn, tmp_path, [_law("T0000001", "噪音管制法")])
    importer.run(conn, tmp_path, Moj())

    resource = _scalar(conn, "SELECT id FROM corpus.legal_resource WHERE local_id = 'T0000001'")
    assert conn.execute(
        "SELECT version_date, title, text_status, authority"
        " FROM corpus.legal_expression WHERE resource_id = %s",
        (resource,),
    ).fetchall() == [(date(2018, 8, 1), "噪音管制法", "available", "official_db")]
    assert conn.execute(
        "SELECT kind, event_date, expression_id IS NOT NULL FROM corpus.lifecycle_event"
        " WHERE resource_id = %s ORDER BY event_date",
        (resource,),
    ).fetchall() == [
        ("enactment", date(1991, 5, 23), False),
        ("full_amendment", date(2018, 8, 1), True),
    ]

    assert conn.execute(
        "SELECT path, status FROM corpus.provisions_at(%s, 'zho', %s) ORDER BY sort_key",
        (resource, date(2020, 1, 1)),
    ).fetchall() == [
        ("art_1", "in_force"),
        ("art_2", "in_force"),
        ("art_2/para_1", "in_force"),
        ("art_2/para_1/pt_1", "in_force"),
        ("art_2/para_1/pt_2", "in_force"),
    ]
    # 公布日 8 月 1 日，第三日起生效
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM corpus.provisions_at(%s, 'zho', %s) WHERE status = 'in_force'",
            resource,
            date(2018, 8, 2),
        )
        == 0
    )
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM corpus.provisions_at(%s, 'zho', %s) WHERE status = 'in_force'",
            resource,
            date(2018, 8, 3),
        )
        == 5
    )
    assert (
        _scalar(
            conn,
            "SELECT corpus.article_text(v.id) FROM corpus.provision_version v"
            " JOIN corpus.provision p ON p.id = v.provision_id"
            " WHERE p.resource_id = %s AND p.path = 'art_2'",
            resource,
        )
        == "本法用詞如下：\n一、噪音。\n二、振動。"
    )
    item = conn.execute("SELECT imported_sha256, parser_version FROM source.item").fetchone()
    assert item is not None and item[1] == PARSER_VERSION


def test_a_law_that_cannot_be_written_is_reported_and_the_rest_goes_in(
    conn: Conn, tmp_path: Path
) -> None:
    # 整批比例過關（不可寫入者不超過 0.5%），單一壞法規才不擋其餘。
    good = [_law(f"T{i:07d}", f"甲{i}法") for i in range(1, 301)]
    bad = _law("T9999999", "壞法", LawLevel="判例")
    _snapshot(conn, tmp_path, [*good, bad])
    importer.run(conn, tmp_path, Moj())

    assert (
        _scalar(conn, "SELECT count(*) FROM corpus.legal_resource WHERE local_id LIKE 'T%'") == 300
    )
    assert conn.execute(
        "SELECT level, code FROM source.import_problem WHERE level = 'error'"
    ).fetchall() == [("error", "law_level")]
    assert _scalar(conn, "SELECT count(*) FROM source.item WHERE imported_sha256 IS NULL") == 1


def test_a_batch_over_the_limits_writes_nothing(conn: Conn, tmp_path: Path) -> None:
    _snapshot(conn, tmp_path, [_law("T0000001", "甲法"), _law("T0000002", "乙法", LawLevel="判例")])
    with pytest.raises(importer.BatchRejected, match="不可寫入"):
        importer.run(conn, tmp_path, Moj())
    assert (
        _scalar(conn, "SELECT count(*) FROM corpus.legal_resource WHERE local_id LIKE 'T000000%'")
        == 0
    )


def test_rerunning_is_a_no_op_and_a_parser_upgrade_finds_nothing_to_change(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _snapshot(conn, tmp_path, [_law("T0000001", "噪音管制法")])
    assert importer.run(conn, tmp_path, Moj()).actions == {"added": 1}
    first = _scalar(conn, "SELECT id FROM corpus.legal_expression WHERE title = '噪音管制法'")

    assert importer.run(conn, tmp_path, Moj()).actions == {"skipped": 1}

    monkeypatch.setattr(importer, "PARSER_VERSION", PARSER_VERSION + 1)
    assert importer.run(conn, tmp_path, Moj()).actions == {"unchanged": 1}
    # 沒有東西改變：沒有新版本，也沒有被取代的列。
    assert (
        _scalar(conn, "SELECT id FROM corpus.legal_expression WHERE title = '噪音管制法'") == first
    )
    assert (
        _scalar(
            conn, "SELECT count(*) FROM corpus.provision_version WHERE superseded_at IS NOT NULL"
        )
        == 0
    )
    assert _scalar(conn, "SELECT parser_version FROM source.item") == PARSER_VERSION + 1


def test_dry_run_writes_nothing(conn: Conn, tmp_path: Path) -> None:
    _snapshot(conn, tmp_path, [_law("T0000001", "噪音管制法")])
    outcome = importer.run(conn, tmp_path, Moj(), dry_run=True)
    assert outcome.metrics.laws == 1
    assert (
        _scalar(conn, "SELECT count(*) FROM corpus.legal_resource WHERE local_id = 'T0000001'") == 0
    )
