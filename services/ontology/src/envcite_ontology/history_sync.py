"""`envcite-history <來源>`：取得法規的歷史版本並補進語料（02-02 §3）。可重跑、冪等。

一部法規的歷史只有一個來源，依種類分：**法律**取自立法院法律系統（`ly`，制定機關）、**命令**取自
主管機關的系統（`moenv`，目前只有環境部的）。依序三步，與 `envcite-sync` 同一套：取得只要網路與
檔案、匯入只要快照與資料庫。

1. **取得**（`envcite_jobkit.run`，一部法規一項：端點錯誤退避重試、斷路器、單項隔離）：來源列出
   目標（`targets`），逐部抓它的各版打包成一份 JSON 快照（內容定址，只存頁面的內容部分），記
   `source.snapshot` 與 `source.item`（鍵見各來源），一部一個交易。過去的版本不會再變，上一份快照
   已有的同一版沿用、不重抓；`--rescan` 全部重抓。
2. **匯入**：每部取最新的快照，對到語料的法規（`resolve`），`backfill.plan` 回推出歷史版本；整批
   比例過驗收才寫，一部一個交易（`backfill.write`），問題記入 `source.import_problem`。寫入時被
   資料庫的約束或型別擋下的那一部（`IntegrityError`、`DataError`）整部不寫、記 error
   `history_write_rejected`，其餘照寫；占對到的法規超過上限，其餘都寫完之後以 2 結束。全國法規
   資料庫的匯入要先跑過：錨點是它寫入的版本。
3. 授權抽取與檢索投影，同 `envcite-sync`（歷史條文的首條與段落也要有）。

結束碼：0 完成；1 暫時性失敗（含隔離超過列舉數的 1%）；2 資料不合預期、整批比例越線或寫入被資料庫
擋下的比例越線；75 斷路器開啟。
來源與授權見 `history_sources.py`；顯示這些文字時須標示出處（快照的 `license_notice`）。
"""

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
from typing import Any, NamedTuple, cast

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
from envcite_ontology import delegation, embedding, import_problems, projection
from envcite_ontology.acquire.web import Http
from envcite_ontology.db import Conn, fetch, fetch_one, scalar
from envcite_ontology.history_sources import (
    SOURCES,
    HistorySource,
    Skip,
    Target,
    Unmatched,
    Web,
)
from envcite_ontology.import_problems import Problem
from envcite_ontology.ingest import audit, backfill, law
from envcite_ontology.ingest.backfill import UnreadablePage
from envcite_ontology.settings import SnapshotSettings, load
from envcite_ontology.verification import config

log = logging.getLogger(__name__)


POLICY = Policy(
    name="history",
    open_action="exit",
    max_attempts=4,
    backoff_base_seconds=2.0,
    backoff_max_seconds=60.0,
)
#: 隔離的法規占列舉數超過此比例，本次判定失敗
COVERAGE_TOLERANCE = 0.01

#: 資料庫以約束或型別擋下的寫入：資料本身不合，重試得到同樣的結果
_REJECTED_BY_DATABASE = (psycopg.errors.IntegrityError, psycopg.errors.DataError)

#: 整批驗收：名稱 → (說明, 上限)；對不到的上限因來源而異（`HistorySource.unmatched_limit`）
LIMITS = {
    "rejected": ("有 error 而不寫的占對到的法規", 0.05),
    "degraded_articles": ("切分退回整條的條占歷史版本的條", audit.LIMITS["degraded_articles"][1]),
    # 寫完才知道，不在寫入前的驗收裡；見 `_write_gate`
    "write_rejected": ("寫入時被資料庫擋下的占對到的法規", 0.05),
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
        """記一行警告（哪一部、為什麼），再交給底層的去處。"""
        log.warning("隔離 %s（%s）：%s", entry.item_id, entry.kind, entry.error)
        self._sink.record(entry)


class BatchRejected(Exception):
    """整批比例越線：轉錄的格式或回推規則跟不上來源。"""


@dataclass
class Report:
    """一次 `envcite-history` 的計數；`line()` 輸出成日誌的一行 JSON。"""

    listed: int = 0
    fetched_pages: int = 0
    reused_pages: int = 0
    snapshots: int = 0
    dead_lettered: int = 0
    matched: int = 0
    skipped: int = 0
    #: 寫入動作 → 部數：`backfill.write` 的回傳，另有沒寫的 `rejected` 與被資料庫擋下的
    #: `write_rejected`。
    actions: Counter[str] = field(default_factory=Counter)
    versions: int = 0
    units: int = 0
    #: 現行頁與全國法規資料庫現行文字的核對結果 → 部數。
    check: Counter[str] = field(default_factory=Counter)
    problems: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0

    def line(self) -> str:
        """報告的一行 JSON（中文不跳脫），寫進日誌。"""
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
    """取得（第一步）：列出目標，逐部抓各版存成快照；回傳結束碼。

    隔離的部數超過列舉數的 1% 回 `ExitCode.FAILED`。
    """
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
        snapshot = scalar(
            conn,
            "INSERT INTO source.snapshot (feed, data_date, sha256, size_bytes, location,"
            "  license_notice) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (source.feed, date.today(), digest, len(data), location, source.attribution),
            int,
        )
        conn.execute(
            "INSERT INTO source.item"
            " (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
            " VALUES (%(feed)s, %(key)s, %(sha)s, %(id)s, %(id)s)"
            " ON CONFLICT (feed, key) DO UPDATE SET content_sha256 = excluded.content_sha256,"
            "   last_snapshot_id = excluded.last_snapshot_id, dropped_on = NULL",
            {"feed": source.feed, "key": target.key, "sha": digest, "id": snapshot},
        )
        report.snapshots += 1


class _LocationRow(NamedTuple):
    location: str


def _latest_bundle(
    conn: Conn, source: HistorySource, root: Path, key: str
) -> dict[str, Any] | None:
    row = fetch_one(
        conn,
        "SELECT s.location FROM source.item AS i"
        "  JOIN source.snapshot AS s ON s.id = i.last_snapshot_id"
        " WHERE i.feed = %s AND i.key = %s",
        (source.feed, key),
        _LocationRow,
    )
    if row is None:
        return None
    path = root / row.location
    return cast(dict[str, Any], json.loads(path.read_text())) if path.exists() else None


@dataclass
class _Prepared:
    item_id: int
    snapshot_id: int
    anchor: backfill.Anchor | None
    plan: backfill.Plan


class _ItemRow(NamedTuple):
    item_id: int
    snapshot_id: int
    location: str


def import_(
    conn: Conn, source: HistorySource, root: Path, report: Report, *, dry_run: bool = False
) -> None:
    """每部取最新快照回推；整批比例不過就一部都不寫（`BatchRejected`）。"""
    metrics = audit.Metrics()
    prepared = _prepare_all(conn, source, root, report, metrics)
    conn.rollback()
    _batch_gate(prepared, metrics, source, report)
    if dry_run:
        return

    for p in prepared:
        action = _write_unless_rejected(conn, source, p)
        report.actions[action] += 1
        if action not in ("rejected", "reshaped", "write_rejected"):
            report.versions += len(p.plan.versions)
            report.units += sum(len(v.units) for v in p.plan.versions)


def _prepare_all(
    conn: Conn, source: HistorySource, root: Path, report: Report, metrics: audit.Metrics
) -> list[_Prepared]:
    """每部最新快照的回推計畫，依鍵排序；來源判定不屬範圍的只計入 `report.skipped`。"""
    prepared: list[_Prepared] = []
    items = fetch(
        conn,
        "SELECT i.id, s.id, s.location FROM source.item AS i"
        "  JOIN source.snapshot AS s ON s.id = i.last_snapshot_id"
        " WHERE i.feed = %s ORDER BY i.key",
        (source.feed,),
        _ItemRow,
    )
    for item in items:
        path = root / item.location
        if not path.exists():
            raise BatchRejected(f"snapshot file missing: {item.location}")
        try:
            anchor, plan = _plan_item(conn, source, json.loads(path.read_text()))
        except Skip:
            report.skipped += 1
            continue
        for parsed in plan.parsed:
            metrics.add(parsed)
        prepared.append(_Prepared(item.item_id, item.snapshot_id, anchor, plan))
    return prepared


def _plan_item(
    conn: Conn, source: HistorySource, bundle: dict[str, Any]
) -> tuple[backfill.Anchor | None, backfill.Plan]:
    """一部法規的回推計畫；對不到語料或頁面讀不出來的，計畫是空的、只帶 error 問題。

    來源判定不屬範圍的（`Skip`）往外拋。
    """
    problems: list[Problem] = []
    try:
        pcode = source.resolve(conn, bundle)
        current, past = source.versions(bundle)
    except UnreadablePage as e:
        problems.append(Problem("error", "unreadable", str(e)))
    except Unmatched as e:
        problems.append(Problem("error", e.code, e.detail))
    else:
        anchor = backfill.load_anchor(conn, pcode)
        if anchor is not None:
            return anchor, backfill.plan(anchor, current, past)
        problems.append(Problem("error", "unmatched", pcode))
    return None, backfill.Plan(problems=problems)


def _batch_gate(
    prepared: list[_Prepared], metrics: audit.Metrics, source: HistorySource, report: Report
) -> None:
    """整批驗收：計數記入報告，任一比例越線就整批不寫（`BatchRejected`）。"""
    matched = [p for p in prepared if p.anchor is not None]
    report.matched = len(matched)
    for p in prepared:
        report.problems.update(f"{x.severity} {x.code}" for x in p.plan.problems)
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


def _write_unless_rejected(conn: Conn, source: HistorySource, p: _Prepared) -> str:
    """寫一部並回傳動作；被資料庫擋下的記成 `write_rejected`，該部不寫。"""
    try:
        return _write_one(conn, source, p)
    except _REJECTED_BY_DATABASE as e:
        # 回推規劃讀的是規劃當時的語料，寫入時資料庫仍可能擋下（約束只認寫入當下的狀態）。
        # 只攔「資料本身不合」的一類；連線、逾時、死結這類暫時性錯誤照舊往外拋，整個 Job 重試。
        detail = _describe(e)
        log.error("寫入被資料庫擋下，該部不寫：%s（%s）", _label(p), detail)
        with conn.transaction():
            _record(conn, p, [*p.plan.problems, Problem("error", "history_write_rejected", detail)])
        return "write_rejected"


def _write_one(conn: Conn, source: HistorySource, p: _Prepared) -> str:
    """一部一個交易；回傳動作（`backfill.write` 的回傳，或沒有寫的 `rejected`）。

    延遲的約束在提交時才檢查，所以提交也在 `try` 之內。
    """
    problems = list(p.plan.problems)
    action = "rejected"
    with conn.transaction():
        if p.anchor is not None and p.plan.ok:
            action = backfill.write(conn, p.anchor, p.plan.versions, source.authority)
            if action == "reshaped":
                problems.append(
                    Problem("error", "history_reshaped", "已寫入的歷史版本日不在這次的目標裡")
                )
        _record(conn, p, problems)
    return action


def _record(conn: Conn, p: _Prepared, problems: list[Problem]) -> None:
    """這部法規這次匯入的問題（每次重寫一份）與匯入記錄。"""
    import_problems.replace(conn, p.item_id, p.snapshot_id, problems)
    conn.execute(
        "UPDATE source.item SET imported_sha256 = content_sha256, parser_version = %s"
        " WHERE id = %s",
        (law.PARSER_VERSION, p.item_id),
    )


def _label(p: _Prepared) -> str:
    return p.anchor.pcode if p.anchor is not None else f"item {p.item_id}"


def _describe(e: psycopg.Error) -> str:
    """資料庫回報的原因，一行：約束名稱與衝突的鍵值都在裡面。"""
    d = e.diag
    return " ".join(filter(None, [d.message_primary, d.message_detail])) or str(e)


def _write_gate(report: Report) -> ExitCode:
    """被資料庫擋下的占對到的法規超過上限就判資料不合預期。

    寫完才知道，所以在其餘法規都寫完、投影也跑完之後才判：寫進去的都是資料庫認可的，
    不必因為少數幾部被擋下而讓整批的檢索停在舊的投影。
    """
    failed = report.actions["write_rejected"]
    if not report.matched:
        return ExitCode.OK
    description, limit = LIMITS["write_rejected"]
    log.info("%s %d／%d", description, failed, report.matched)
    if failed / report.matched > limit:
        log.error("%s %.2f%%，上限 %.2f%%", description, failed / report.matched * 100, limit * 100)
        return ExitCode.FATAL
    return ExitCode.OK


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
    """依 `step` 跑取得與匯入，回傳結束碼。

    `sync` 兩者都跑，匯入後抽取授權並重建檢索投影。結束碼的意義見模組說明。`dry_run` 只回推
    與驗收，不寫也不重建投影。
    """
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
                code = _write_gate(report)
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
    """`envcite-history <來源> [步驟]`：取得法規的歷史版本並補進語料。

    步驟 `acquire` 只取得各版存成快照，`import` 只回推並寫入語料，預設的 `sync` 兩者都跑。
    """
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
        embedder = config.embedder_from_settings(settings, timeout=60.0)
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
