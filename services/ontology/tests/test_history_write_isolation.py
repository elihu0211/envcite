"""歷史版本匯入：一部法規的寫入被資料庫擋下，不拖垮整批（`history_sync.import_`）。

兩部命令，鍵排在前的第一部寫入被擋下、第二部照寫。規劃讀的是規劃當時的語料，寫入之前另一個
作業入庫的版本規劃看不到，所以擋下的方式是在寫入前植入同版本日的官方版本，對真實的綱要。
"""

from datetime import date
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
from psycopg.types.range import Range

from envcite_jobkit import Policy
from envcite_ontology import history_sync
from envcite_ontology.acquire import moenv_law
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.history_sources import Moenv
from envcite_ontology.ingest import backfill, importer
from tests.fake_web import FakeWeb
from tests.moenv_site import listing, site
from tests.sample import stored_snapshot
from tests.test_backfill import HISTORY, V1, V1_TEXT, V2, V2_TEXT, V3, V3_TEXT
from tests.test_import import _law

Conn = psycopg.Connection[tuple[object, ...]]

FIRST = ("T0000001", "FL000001", "噪音管制法施行細則")
SECOND = ("T0000002", "FL000002", "空氣污染防制法施行細則")
POLICY = Policy(name="test", max_attempts=1, backoff_base_seconds=0.0)


@pytest.fixture(autouse=True)
def _no_batch_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """兩部法規的比例沒有意義；個別測試自己設要驗的上限。"""
    monkeypatch.setattr(
        history_sync, "LIMITS", {k: (d, 1.0) for k, (d, _) in history_sync.LIMITS.items()}
    )


def _anchors(conn: Conn, root: Path) -> None:
    articles = [
        {"ArticleType": "A", "ArticleNo": f"第 {n} 條", "ArticleContent": t} for n, t in V3_TEXT
    ]
    stored_snapshot(
        conn,
        root,
        [
            _law(
                pcode,
                title,
                LawLevel="命令",
                LawHistories=HISTORY,
                LawModifiedDate="20200301",
                LawArticles=articles,
            )
            for pcode, _, title in (FIRST, SECOND)
        ],
    )
    importer.run(conn, root, Moj())
    conn.commit()


def _pages() -> dict[str, str]:
    past = {12: (V2, V2_TEXT), 11: (V1, V1_TEXT)}
    pages: dict[str, str] = {}
    for _, site_id, title in (FIRST, SECOND):
        pages |= site(site_id, title, (V3, V3_TEXT), past)
    pages[moenv_law.LIST.format(category="1601", page=1)] = listing(
        [(site_id, title, "命令") for _, site_id, title in (FIRST, SECOND)]
    )
    return pages


def _sync(conn: Conn, root: Path, source: Moenv | None = None) -> int:
    return history_sync.sync(
        conn,
        source or Moenv(),
        root,
        FakeWeb(_pages()),
        policy=POLICY,
        sleep=lambda _: None,
    )


def _race(monkeypatch: pytest.MonkeyPatch, pcode: str) -> None:
    """規劃之後、寫入之前，另一個作業把同版本日的官方版本入了庫。"""
    real = backfill.write

    def write(conn: Conn, anchor: backfill.Anchor, target: Any, authority: str) -> str:
        if anchor.pcode == pcode:
            conn.execute(
                "INSERT INTO corpus.legal_expression (resource_id, language, version_date, title,"
                "  text_period, text_status, text_authority)"
                " VALUES (%s, 'zho', %s, 'x', %s, 'available', 'official_db')",
                (anchor.resource, V1, Range(V1, V2, "[)")),
            )
        return real(conn, anchor, target, authority)

    monkeypatch.setattr(backfill, "write", write)


def _versions(conn: Conn, pcode: str) -> set[date]:
    return {
        cast(date, d)
        for (d,) in conn.execute(
            "SELECT e.version_date FROM corpus.legal_expression AS e"
            "  JOIN corpus.legal_resource AS r ON r.id = e.resource_id"
            " WHERE r.local_id = %s",
            (pcode,),
        )
    }


def _problems(conn: Conn, pcode: str) -> list[tuple[str, str, str]]:
    return [
        (str(s), str(c), str(d))
        for s, c, d in conn.execute(
            "SELECT p.severity, p.problem_code, p.detail FROM source.import_problem AS p"
            "  JOIN source.item AS i ON i.id = p.item_id"
            " WHERE i.key = %s ORDER BY p.id",
            (next(site_id for code, site_id, _ in (FIRST, SECOND) if code == pcode),),
        )
    ]


def test_a_law_the_database_refuses_is_recorded_and_the_rest_are_written(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchors(conn, tmp_path)
    _race(monkeypatch, FIRST[0])

    assert _sync(conn, tmp_path) == 0

    assert _versions(conn, SECOND[0]) == {V1, V2, V3}
    # 被擋下的那部整部不寫：交易退回，只剩官方資料庫的版本
    assert _versions(conn, FIRST[0]) == {V3}
    ((severity, code, detail),) = _problems(conn, FIRST[0])
    assert (severity, code) == ("error", "history_write_rejected")
    # 資料庫回報的約束名稱與衝突的鍵留在報告裡，沒有被吞掉
    assert "legal_expression_resource_id_language_version_date_key" in detail
    assert str(V1) in detail
    assert _problems(conn, SECOND[0]) == []


def test_a_rerun_writes_the_law_once_the_conflict_is_gone(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchors(conn, tmp_path)
    with monkeypatch.context() as m:
        _race(m, FIRST[0])
        assert _sync(conn, tmp_path) == 0

    assert _sync(conn, tmp_path) == 0

    assert _versions(conn, FIRST[0]) == {V1, V2, V3}
    assert _problems(conn, FIRST[0]) == []  # 問題每次匯入重寫一份，不留舊的


def test_refusals_over_the_limit_end_with_fatal_after_the_rest_are_written(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchors(conn, tmp_path)
    description, _ = history_sync.LIMITS["write_rejected"]
    monkeypatch.setitem(history_sync.LIMITS, "write_rejected", (description, 0.4))
    _race(monkeypatch, FIRST[0])

    assert _sync(conn, tmp_path) == 2  # 兩部有一部被擋下：50%，超過 40%

    assert _versions(conn, SECOND[0]) == {V1, V2, V3}  # 其餘照寫
    assert _problems(conn, FIRST[0])[0][:2] == ("error", "history_write_rejected")


def test_a_value_the_database_rejects_by_type_is_isolated_too(conn: Conn, tmp_path: Path) -> None:
    class Bogus(Moenv):
        authority = "not_an_authority"  # 列舉沒有這個值：資料庫回 DataError

    _anchors(conn, tmp_path)

    assert _sync(conn, tmp_path, Bogus()) == 0

    assert _versions(conn, FIRST[0]) == {V3}
    assert _problems(conn, FIRST[0])[0][:2] == ("error", "history_write_rejected")


def test_a_transient_database_error_still_stops_the_job_for_a_retry(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _anchors(conn, tmp_path)

    def write(conn: Conn, anchor: backfill.Anchor, target: Any, authority: str) -> str:
        conn.execute("SET LOCAL statement_timeout = '1ms'")
        conn.execute("SELECT pg_sleep(1)")
        raise AssertionError("unreachable")

    monkeypatch.setattr(backfill, "write", write)

    with pytest.raises(psycopg.errors.QueryCanceled):
        _sync(conn, tmp_path)
