"""快照 → 語料。

每個來源取最新的一份快照，逐部解析；整批比例不過驗收（`audit`）就一部都不寫。通過後
逐部法規在各自的交易內寫入，所以中途失敗只重做未完成的部分，而不是整批。每部法規的
動作（新增、新增版本、更正、更新、拒絕）見 `write.apply`。
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from envcite_ontology import import_problems
from envcite_ontology.acquire.engine import Source
from envcite_ontology.acquire.moj import content_sha256, load_laws, pcode
from envcite_ontology.db import Conn
from envcite_ontology.import_problems import Problem
from envcite_ontology.ingest import law as parsing
from envcite_ontology.ingest.audit import Metrics
from envcite_ontology.ingest.law import PARSER_VERSION, ParsedLaw
from envcite_ontology.ingest.write import apply

log = logging.getLogger(__name__)


class BatchRejected(Exception):
    """整批比例越線：解析規則跟不上來源的格式。"""


@dataclass(frozen=True)
class Parsed:
    """一部解析過的法規，連同來源項目的鍵與內容雜湊（用來判斷是否已匯入）。"""

    key: str
    sha256: bytes
    law: ParsedLaw


@dataclass
class Outcome:
    """一次匯入的結果：整批指標與各動作的法規數。"""

    metrics: Metrics
    # 各動作的法規數：added、versioned、corrected、refreshed、unchanged、regressed；
    # skipped 是內容雜湊與解析器版本皆同者，rejected 是解析出 error 者。
    actions: Counter[str]


@dataclass(frozen=True)
class _Batch:
    """一個來源檔最新快照的解析結果。"""

    feed: str
    snapshot_id: int
    parsed: list[Parsed]


@dataclass(frozen=True)
class _Stored:
    """來源項目已記下的匯入狀態；從未匯入者的雜湊與版本為空。"""

    item_id: object
    sha256: object
    parser_version: object


def run(conn: Conn, snapshot_dir: Path, source: Source, *, dry_run: bool = False) -> Outcome:
    """取每個來源最新的快照解析、驗收，通過後逐部寫入語料；回傳指標與各動作的法規數。

    整批比例越線丟 `BatchRejected`，此時一部都不寫；快照檔不存在丟 `InvalidSnapshot`。
    `dry_run` 只解析與驗收。每部法規在自己的交易內寫入並更新來源項目的匯入狀態，
    所以中途失敗只重做未完成的部分。
    """
    metrics = Metrics()
    actions: Counter[str] = Counter()
    batches: list[_Batch] = []
    for feed in source.feeds:
        batch = _parse_feed(conn, snapshot_dir, feed.name, metrics)
        if batch is not None:
            batches.append(batch)

    for line in metrics.lines():
        log.info("%s", line)
    if problems := metrics.violations():
        raise BatchRejected("；".join(problems))
    if dry_run:
        return Outcome(metrics, actions)

    for batch in batches:
        _write_batch(conn, batch, actions)
    return Outcome(metrics, actions)


def _parse_feed(conn: Conn, snapshot_dir: Path, feed_name: str, metrics: Metrics) -> _Batch | None:
    """解析來源檔最新的快照並計入 `metrics`；沒有快照為 None。"""
    row = conn.execute(
        "SELECT id, data_date, location FROM source.snapshot"
        " WHERE feed = %s ORDER BY data_date DESC, id DESC LIMIT 1",
        (feed_name,),
    ).fetchone()
    if row is None:
        log.info("%s: 沒有快照", feed_name)
        return None
    snapshot_id, data_date, location = row
    assert isinstance(snapshot_id, int) and isinstance(data_date, date)
    _, laws = load_laws(snapshot_dir / str(location))  # 檔案不存在：InvalidSnapshot
    parsed = [
        Parsed(
            pcode(raw),
            content_sha256(raw),
            parsing.parse(raw, pcode=pcode(raw), data_date=data_date),
        )
        for raw in laws
    ]
    for p in parsed:
        metrics.add(p.law)
    return _Batch(feed_name, snapshot_id, parsed)


def _write_batch(conn: Conn, batch: _Batch, actions: Counter[str]) -> None:
    state = {
        str(key): _Stored(item_id, sha, version)
        for item_id, key, sha, version in conn.execute(
            "SELECT id, key, imported_sha256, parser_version FROM source.item WHERE feed = %s",
            (batch.feed,),
        )
    }
    # 結束讀取用的交易：之後每部法規是自己的交易，不是同一個交易裡的存檔點。
    conn.commit()
    for p in batch.parsed:
        _write_law(conn, p, state[p.key], batch.snapshot_id, actions)
    log.info("%s: %s", batch.feed, "，".join(f"{k} {v}" for k, v in sorted(actions.items())))


def _write_law(
    conn: Conn, p: Parsed, stored: _Stored, snapshot_id: int, actions: Counter[str]
) -> None:
    if stored.sha256 == p.sha256 and stored.parser_version == PARSER_VERSION:
        actions["skipped"] += 1
        return
    with conn.transaction():
        problems_now = list(p.law.problems)
        applied = None
        if p.law.ok:
            applied = apply(conn, p.law, first_import=stored.sha256 is None)
            if applied.action == "regressed":
                problems_now.append(Problem("error", "version_regressed", "版本日早於既有最新版本"))
            elif applied.action == "corrected":
                problems_now.append(
                    Problem("warning", "corrected", f"{applied.changed} 個單元的文字改變")
                )
        import_problems.replace(conn, stored.item_id, snapshot_id, problems_now)
        if applied is None:
            actions["rejected"] += 1
        elif applied.action == "regressed":
            actions["regressed"] += 1
        else:
            actions[applied.action] += 1
            conn.execute(
                "UPDATE source.item SET imported_sha256 = %s, parser_version = %s WHERE id = %s",
                (p.sha256, PARSER_VERSION, stored.item_id),
            )
