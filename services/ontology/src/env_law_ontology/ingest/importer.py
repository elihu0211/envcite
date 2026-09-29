"""快照 → 語料（env-law-docs 03-03 §3、§8）。

每個來源取最新的一份快照，逐部解析；整批比例不過驗收（`audit`）就一部都不寫。通過後
逐部法規在各自的交易內寫入，所以中途失敗只重做未完成的部分，而不是整批。

首次匯入：每部法規只有現行版本。已匯入的法規內容又改變時，需要的是「新增一個版本」而
非覆寫，那是增量匯入，尚未實作——這裡只記 `changed_after_import`，不動既有的語料。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from psycopg.types.range import Range

from env_law_ontology.acquire.moj import Moj, content_sha256, load_laws, pcode
from env_law_ontology.ingest import law as parsing
from env_law_ontology.ingest.audit import Metrics
from env_law_ontology.ingest.law import PARSER_VERSION, ParsedLaw, Problem

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]


class BatchRejected(Exception):
    """整批比例越線：解析規則跟不上來源的格式。"""


@dataclass(frozen=True)
class Parsed:
    key: str
    sha256: bytes
    law: ParsedLaw


def run(conn: Conn, snapshot_dir: Path, source: Moj, *, dry_run: bool = False) -> Metrics:
    metrics = Metrics()
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
        _, laws = load_laws(snapshot_dir / str(location))
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
        return metrics

    for feed_name, snapshot_id, parsed in batches:
        written = skipped = 0
        state = {
            str(key): (item_id, sha, version)
            for item_id, key, sha, version in conn.execute(
                "SELECT id, key, imported_sha256, parser_version FROM source.item WHERE feed = %s",
                (feed_name,),
            )
        }
        for p in parsed:
            item_id, imported_sha, imported_version = state[p.key]
            if imported_sha == p.sha256 and imported_version == PARSER_VERSION:
                skipped += 1
                continue
            problems_now = list(p.law.problems)
            if imported_sha is not None and imported_sha != p.sha256:
                problems_now.append(
                    Problem("error", "changed_after_import", "內容已改變；增量匯入尚未實作")
                )
            with conn.transaction():
                _replace_problems(conn, item_id, snapshot_id, problems_now)
                if p.law.ok and not any(x.code == "changed_after_import" for x in problems_now):
                    _write(conn, p.law)
                    conn.execute(
                        "UPDATE source.item SET imported_sha256 = %s, parser_version = %s"
                        " WHERE id = %s",
                        (p.sha256, PARSER_VERSION, item_id),
                    )
                    written += 1
        log.info("%s: 寫入 %d 部，未變 %d 部", feed_name, written, skipped)
    conn.commit()
    return metrics


def _replace_problems(
    conn: Conn, item_id: object, snapshot_id: int, problems: list[Problem]
) -> None:
    conn.execute("DELETE FROM source.import_problem WHERE item_id = %s", (item_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO source.import_problem (item_id, snapshot_id, level, code, detail)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(item_id, snapshot_id, p.level, p.code, p.detail) for p in problems],
        )


def _write(conn: Conn, law: ParsedLaw) -> None:
    assert law.version_date is not None
    row = conn.execute(
        "INSERT INTO corpus.legal_resource (id_scheme, local_id, kind, document_type)"
        " VALUES ('moj_pcode', %s, %s::corpus.norm_kind, %s)"
        " ON CONFLICT (id_scheme, local_id) DO UPDATE"
        "   SET kind = EXCLUDED.kind, document_type = EXCLUDED.document_type"
        " RETURNING id",
        (law.pcode, law.kind, law.document_type),
    ).fetchone()
    assert row is not None
    resource = row[0]
    # 取代：先前的內容（樣本，或舊版解析器的產出）整部重寫。
    for table in ("provision", "lifecycle_event", "legal_expression"):
        conn.execute(f"DELETE FROM corpus.{table} WHERE resource_id = %s", (resource,))

    row = conn.execute(
        "INSERT INTO corpus.legal_expression"
        " (resource_id, language, version_date, title, text_period, text_status, authority)"
        " VALUES (%s, 'zho', %s, %s, daterange(%s, NULL), %s::corpus.text_status, 'official_db')"
        " RETURNING id",
        (
            resource,
            law.version_date,
            law.title,
            law.version_date,
            "available" if law.units else "unavailable",
        ),
    ).fetchone()
    assert row is not None
    expression = row[0]

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO corpus.lifecycle_event"
            " (resource_id, kind, event_date, document_no, expression_id, source_text)"
            " VALUES (%s, %s::corpus.event_kind, %s, %s, %s, %s)",
            [
                (
                    resource,
                    e.kind,
                    e.date,
                    e.document_no,
                    expression if i == law.version_event else None,
                    e.text,
                )
                for i, e in enumerate(law.events)
            ],
        )
        by_depth: dict[int, list[parsing.ParsedUnit]] = {}
        for u in law.units:
            by_depth.setdefault(u.path.count("/"), []).append(u)
        for depth in sorted(by_depth):
            cur.executemany(
                "INSERT INTO corpus.provision"
                " (resource_id, parent_id, provision_type, path, sort_key)"
                " VALUES (%s,"
                "         (SELECT id FROM corpus.provision WHERE resource_id = %s AND path = %s),"
                "         %s::corpus.provision_type, %s, %s)",
                [
                    (
                        resource,
                        resource,
                        u.path.rsplit("/", 1)[0] if depth else None,
                        u.type,
                        u.path,
                        u.sort_key,
                    )
                    for u in by_depth[depth]
                ],
            )
        cur.executemany(
            "INSERT INTO corpus.provision_version"
            " (provision_id, expression_id, resource_id, content, in_force, commencement)"
            " VALUES ((SELECT id FROM corpus.provision WHERE resource_id = %s AND path = %s),"
            "         %s, %s, %s, %s, %s::corpus.commencement_rule)",
            [
                (
                    resource,
                    u.path,
                    expression,
                    resource,
                    u.content,
                    Range(u.starts, u.ends, "[)") if u.starts else None,
                    u.rule,
                )
                for u in law.units
            ],
        )
