"""`envcite-history <來源>`：取得法規的歷史版本並補進語料（02-02 §3）。可重跑、冪等。

一部法規的歷史只有一個來源，依種類分：**法律**取自立法院法律系統（`ly`，制定機關）、**命令**取自
主管機關的系統（`moenv`，目前只有環境部的）。依序三步，與 `envcite-sync` 同一套：取得只要網路與
檔案、匯入只要快照與資料庫。

1. **取得**（`envcite_jobkit.run`，一部法規一項：端點錯誤退避重試、斷路器、單項隔離）：來源列出
   目標（`targets`），逐部抓它的各版打包成一份 JSON 快照（內容定址，只存頁面的內容部分），記
   `source.snapshot` 與 `source.item`（鍵見各來源），一部一個交易。過去的版本不會再變，上一份快照
   已有的同一版沿用、不重抓；`--rescan` 全部重抓。
2. **匯入**：每部取最新的快照，對到語料的法規（`resolve`），`backfill.plan` 回推出歷史版本；整批
   比例過驗收才寫，一部一個交易（`backfill.write`），問題記入 `source.import_problem`。全國法規
   資料庫的匯入要先跑過：錨點是它寫入的版本。
3. 授權抽取與檢索投影，同 `envcite-sync`（歷史條文的首條與段落也要有）。

結束碼：0 完成；1 暫時性失敗（含隔離超過列舉數的 1%）；2 資料不合預期或整批比例越線；75 斷路器開啟。
來源與授權見 `history_sources.py`；顯示這些文字時須標示出處（快照的 `license_notice`）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

import psycopg

from envcite_jobkit import (
    DeadLetter,
    ExitCode,
    ItemError,
    JsonlDeadLetterSink,
    MemoryDeadLetterSink,
    Policy,
    RunStats,
    run,
)
from envcite_ontology import delegation, embedding, projection
from envcite_ontology.acquire.web import Http
from envcite_ontology.history_sources import (
    SOURCES,
    HistorySource,
    Skip,
    Target,
    Unmatched,
    Web,
)
from envcite_ontology.ingest import audit, backfill, law
from envcite_ontology.ingest.backfill import UnreadablePage
from envcite_ontology.ingest.law import Problem
from envcite_ontology.settings import SnapshotSettings, load

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

POLICY = Policy(
    name="history",
    open_action="exit",
    max_attempts=4,
    backoff_base_seconds=2.0,
    backoff_max_seconds=60.0,
)
#: 隔離的法規占列舉數超過此比例，本次判定失敗
COVERAGE_TOLERANCE = 0.01

#: 整批驗收：名稱 → (說明, 上限)；對不到的上限因來源而異（`HistorySource.unmatched_limit`）
LIMITS = {
    "rejected": ("有 error 而不寫的占對到的法規", 0.05),
    "degraded_articles": ("切分退回整條的條占歷史版本的條", audit.LIMITS["degraded_articles"][1]),
}


class _LoggedSink:
    """隔離時記一行日誌（哪一部、為什麼），再交給政策指定的去處。"""

    def __init__(self, policy: Policy) -> None:
        self._sink: JsonlDeadLetterSink | MemoryDeadLetterSink = (
            JsonlDeadLetterSink(policy.dead_letter_path)
            if policy.dead_letter_path
            else MemoryDeadLetterSink()
        )

    def record(self, entry: DeadLetter) -> None:
        log.warning("隔離 %s（%s）：%s", entry.item_id, entry.kind, entry.error)
        self._sink.record(entry)


class BatchRejected(Exception):
    """整批比例越線：轉錄的格式或回推規則跟不上來源。"""


@dataclass
class Report:
    listed: int = 0
    fetched_pages: int = 0
    reused_pages: int = 0
    snapshots: int = 0
    dead_lettered: int = 0
    matched: int = 0
    skipped: int = 0
    actions: Counter[str] = field(default_factory=Counter)
    versions: int = 0
    units: int = 0
    check: Counter[str] = field(default_factory=Counter)
    problems: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0

    def line(self) -> str:
        return json.dumps(
            {k: dict(v) if isinstance(v, Counter) else v for k, v in self.__dict__.items()},
            ensure_ascii=False,
        )


def acquire(
    conn: Conn,
    source: HistorySource,
    root: Path,
    web: Web,
    report: Report,
    *,
    rescan: bool = False,
    policy: Policy = POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> ExitCode:
    listed = source.targets(conn, web)
    conn.rollback()
    report.listed = len(listed)
    stats = RunStats()
    code = run(
        listed,
        lambda t: _acquire_one(conn, source, root, web, t, report, rescan=rescan),
        policy,
        item_id=lambda t: t.key,
        sink=_LoggedSink(policy),
        stats=stats,
        sleep=sleep,
    )
    report.dead_lettered = stats.dead_lettered
    if code is ExitCode.OK and stats.dead_lettered > COVERAGE_TOLERANCE * max(len(listed), 1):
        log.error("隔離 %d 部，超過列舉數 %d 的 1%%", stats.dead_lettered, len(listed))
        return ExitCode.FAILED
    return code


def _acquire_one(
    conn: Conn,
    source: HistorySource,
    root: Path,
    web: Web,
    target: Target,
    report: Report,
    *,
    rescan: bool,
) -> None:
    previous: dict[str, Any] = {}
    if not rescan:
        latest = _latest_bundle(conn, source, root, target.key)
        conn.rollback()
        previous = cast(dict[str, Any], latest.get("versions", {})) if latest else {}
    try:
        bundle = source.bundle(web, target, previous)
    except UnreadablePage as e:
        raise ItemError(f"{target.key}: {e}") from e
    report.fetched_pages += bundle.pop("_fetched")
    report.reused_pages += bundle.pop("_reused")
    data = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(data).digest()
    location = f"{source.feed}/{digest.hex()}"
    path = root / location
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    with conn.transaction():
        known = conn.execute(
            "SELECT id FROM source.snapshot WHERE feed = %s AND sha256 = %s",
            (source.feed, digest),
        ).fetchone()
        if known is not None:
            return
        snapshot = conn.execute(
            "INSERT INTO source.snapshot (feed, data_date, sha256, size_bytes, location,"
            "  license_notice) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (source.feed, date.today(), digest, len(data), location, source.attribution),
        ).fetchone()
        assert snapshot is not None
        conn.execute(
            "INSERT INTO source.item"
            " (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
            " VALUES (%(feed)s, %(key)s, %(sha)s, %(id)s, %(id)s)"
            " ON CONFLICT (feed, key) DO UPDATE SET content_sha256 = excluded.content_sha256,"
            "   last_snapshot_id = excluded.last_snapshot_id, dropped_on = NULL",
            {"feed": source.feed, "key": target.key, "sha": digest, "id": snapshot[0]},
        )
        report.snapshots += 1


def _latest_bundle(
    conn: Conn, source: HistorySource, root: Path, key: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT s.location FROM source.item AS i"
        "  JOIN source.snapshot AS s ON s.id = i.last_snapshot_id"
        " WHERE i.feed = %s AND i.key = %s",
        (source.feed, key),
    ).fetchone()
    if row is None:
        return None
    path = root / str(row[0])
    return cast(dict[str, Any], json.loads(path.read_text())) if path.exists() else None


@dataclass
class _Prepared:
    item_id: int
    snapshot_id: int
    anchor: backfill.Anchor | None
    plan: backfill.Plan


def import_(
    conn: Conn, source: HistorySource, root: Path, report: Report, *, dry_run: bool = False
) -> None:
    """每部取最新快照回推；整批比例不過就一部都不寫（`BatchRejected`）。"""
    prepared: list[_Prepared] = []
    metrics = audit.Metrics()
    items = conn.execute(
        "SELECT i.id, s.id, s.location FROM source.item AS i"
        "  JOIN source.snapshot AS s ON s.id = i.last_snapshot_id"
        " WHERE i.feed = %s ORDER BY i.key",
        (source.feed,),
    ).fetchall()
    for item_id, snapshot_id, location in items:
        path = root / str(location)
        if not path.exists():
            raise BatchRejected(f"snapshot file missing: {location}")
        bundle = json.loads(path.read_text())
        problems: list[Problem] = []
        anchor = None
        plan = backfill.Plan(problems=problems)
        try:
            pcode = source.resolve(conn, bundle)
            current, past = source.versions(bundle)
        except Skip:
            report.skipped += 1
            continue
        except UnreadablePage as e:
            problems.append(Problem("error", "unreadable", str(e)))
        except Unmatched as e:
            problems.append(Problem("error", e.code, e.detail))
        else:
            anchor = backfill.load_anchor(conn, pcode)
            if anchor is None:
                problems.append(Problem("error", "unmatched", pcode))
            else:
                plan = backfill.plan(anchor, current, past)
        for parsed in plan.parsed:
            metrics.add(parsed)
        prepared.append(_Prepared(cast(int, item_id), cast(int, snapshot_id), anchor, plan))
    conn.rollback()

    matched = [p for p in prepared if p.anchor is not None]
    report.matched = len(matched)
    for p in prepared:
        report.problems.update(f"{x.level} {x.code}" for x in p.plan.problems)
        report.check.update(p.plan.check)
    limits = LIMITS | {"unmatched": ("對不到語料法規的占列舉數", source.unmatched_limit)}
    ratios = {
        "unmatched": (len(prepared) - len(matched), len(prepared)),
        "rejected": (sum(not p.plan.ok for p in matched), len(matched)),
        "degraded_articles": (metrics.degraded_articles, metrics.articles),
    }
    for name, (n, d) in ratios.items():
        log.info("%s %d／%d", limits[name][0], n, d)
    violations = [
        f"{limits[n][0]} {k / d:.2%}，上限 {limits[n][1]:.2%}"
        for n, (k, d) in ratios.items()
        if d and k / d > limits[n][1]
    ]
    if not prepared and not report.skipped:
        violations.append("沒有任何快照")
    if violations:
        raise BatchRejected("；".join(violations))
    if dry_run:
        return

    for p in prepared:
        with conn.transaction():
            problems = list(p.plan.problems)
            if p.anchor is not None and p.plan.ok:
                action = backfill.write(conn, p.anchor, p.plan.versions, source.authority)
                if action == "reshaped":
                    problems.append(
                        Problem("error", "history_reshaped", "已寫入的歷史版本日不在這次的目標裡")
                    )
                else:
                    report.versions += len(p.plan.versions)
                    report.units += sum(len(v.units) for v in p.plan.versions)
                report.actions[action] += 1
            else:
                report.actions["rejected"] += 1
            conn.execute("DELETE FROM source.import_problem WHERE item_id = %s", (p.item_id,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO source.import_problem"
                    " (item_id, snapshot_id, severity, problem_code, detail)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    [(p.item_id, p.snapshot_id, x.level, x.code, x.detail) for x in problems],
                )
            conn.execute(
                "UPDATE source.item SET imported_sha256 = content_sha256, parser_version = %s"
                " WHERE id = %s",
                (law.PARSER_VERSION, p.item_id),
            )


def sync(
    conn: Conn,
    source: HistorySource,
    root: Path,
    web: Web,
    *,
    step: str = "sync",
    rescan: bool = False,
    dry_run: bool = False,
    embedder: embedding.Embedder | None = None,
    policy: Policy = POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    started = time.monotonic()
    report = Report()
    code: int = ExitCode.OK
    try:
        if step in ("sync", "acquire"):
            code = acquire(
                conn, source, root, web, report, rescan=rescan, policy=policy, sleep=sleep
            )
        if code == ExitCode.OK and step in ("sync", "import"):
            import_(conn, source, root, report, dry_run=dry_run)
            if not dry_run:
                delegation.extract(conn)
                conn.commit()
                projection.rebuild(conn, embedder, policy)
    except BatchRejected as e:
        conn.rollback()
        log.error("history import rejected: %s", e)
        code = ExitCode.FATAL
    except projection.EmbeddingStopped as e:
        conn.rollback()
        code = e.code
    report.seconds = round(time.monotonic() - started, 1)
    log.info("歷史版本（%s）：%s", source.feed, report.line())
    return int(code)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-history")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("step", nargs="?", default="sync", choices=("sync", "acquire", "import"))
    parser.add_argument("--rescan", action="store_true", help="過去的版本全部重抓")
    parser.add_argument("--dry-run", action="store_true", help="匯入只回推與驗收，不寫")
    args = parser.parse_args()

    source = SOURCES[args.source]()
    settings = load(SnapshotSettings)
    with psycopg.connect(settings.dsn) as conn:
        embedder = embedding.from_settings(settings, timeout=60.0)
        policy = Policy.load(defaults=POLICY)
        raise SystemExit(
            sync(
                conn,
                source,
                settings.snapshot_dir,
                Http(source.pause, legacy_tls=source.legacy_tls),
                step=args.step,
                rescan=args.rescan,
                dry_run=args.dry_run,
                embedder=embedder,
                policy=policy,
            )
        )
