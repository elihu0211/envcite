"""快照 → 語料（env-law-docs 03-03 §3、03-04 §4）。

每個來源取最新的一份快照，逐部解析；整批比例不過驗收（`audit`）就一部都不寫。通過後
逐部法規在各自的交易內寫入，所以中途失敗只重做未完成的部分，而不是整批。每部法規的
動作（新增、新增版本、更正、更新、拒絕）見 `write.apply`。
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from env_law_ontology.acquire.engine import Source
from env_law_ontology.acquire.moj import content_sha256, load_laws, pcode
from env_law_ontology.ingest import law as parsing
from env_law_ontology.ingest.audit import Metrics
from env_law_ontology.ingest.law import PARSER_VERSION, ParsedLaw, Problem
from env_law_ontology.ingest.write import apply

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]


class BatchRejected(Exception):
    """整批比例越線：解析規則跟不上來源的格式。"""


@dataclass(frozen=True)
class Parsed:
    key: str
    sha256: bytes
    law: ParsedLaw


@dataclass
class Outcome:
    metrics: Metrics
    # 各動作的法規數：added、versioned、corrected、refreshed、unchanged、regressed；
    # skipped 是內容雜湊與解析器版本皆同者，rejected 是解析出 error 者。
    actions: Counter[str]


def run(conn: Conn, snapshot_dir: Path, source: Source, *, dry_run: bool = False) -> Outcome:
    metrics = Metrics()
    actions: Counter[str] = Counter()
    batches: list[tuple[str, int, list[Parsed]]] = []
    for feed in source.feeds:
        row = conn.execute(
            "SELECT id, data_date, location FROM source.snapshot"
            " WHERE feed = %s ORDER BY data_date DESC, id DESC LIMIT 1",
            (feed.name,),
        ).fetchone()
        if row is None:
            log.info("%s: 沒有快照", feed.name)
            continue
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
        batches.append((feed.name, snapshot_id, parsed))

    for line in metrics.lines():
        log.info("%s", line)
    if problems := metrics.violations():
        raise BatchRejected("；".join(problems))
    if dry_run:
        return Outcome(metrics, actions)

    for feed_name, snapshot_id, parsed in batches:
        state = {
            str(key): (item_id, sha, version)
            for item_id, key, sha, version in conn.execute(
                "SELECT id, key, imported_sha256, parser_version FROM source.item WHERE feed = %s",
                (feed_name,),
            )
        }
        # 結束讀取用的交易：之後每部法規是自己的交易，不是同一個交易裡的存檔點。
        conn.commit()
        for p in parsed:
            item_id, imported_sha, imported_version = state[p.key]
            if imported_sha == p.sha256 and imported_version == PARSER_VERSION:
                actions["skipped"] += 1
                continue
            with conn.transaction():
                problems_now = list(p.law.problems)
                applied = None
                if p.law.ok:
                    applied = apply(conn, p.law, first_import=imported_sha is None)
                    if applied.action == "regressed":
                        problems_now.append(
                            Problem("error", "version_regressed", "版本日早於既有最新版本")
                        )
                    elif applied.action == "corrected":
                        problems_now.append(
                            Problem("warning", "corrected", f"{applied.changed} 個單元的文字改變")
                        )
                _replace_problems(conn, item_id, snapshot_id, problems_now)
                if applied is None:
                    actions["rejected"] += 1
                elif applied.action == "regressed":
                    actions["regressed"] += 1
                else:
                    actions[applied.action] += 1
                    conn.execute(
                        "UPDATE source.item SET imported_sha256 = %s, parser_version = %s"
                        " WHERE id = %s",
                        (p.sha256, PARSER_VERSION, item_id),
                    )
        log.info("%s: %s", feed_name, "，".join(f"{k} {v}" for k, v in sorted(actions.items())))
    return Outcome(metrics, actions)


def _replace_problems(
    conn: Conn, item_id: object, snapshot_id: int, problems: list[Problem]
) -> None:
    conn.execute("DELETE FROM source.import_problem WHERE item_id = %s", (item_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO source.import_problem"
            " (item_id, snapshot_id, severity, problem_code, detail)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(item_id, snapshot_id, p.level, p.code, p.detail) for p in problems],
        )
