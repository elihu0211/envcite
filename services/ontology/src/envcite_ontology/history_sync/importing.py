"""匯入（第二步）：每部取最新的快照回推歷史版本，整批驗收後一部一個交易寫入。"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import psycopg

from envcite_ontology import import_problems
from envcite_ontology.db import Conn, fetch
from envcite_ontology.history_sources import HistorySource, Skip, Unmatched
from envcite_ontology.history_sync.policy import LIMITS
from envcite_ontology.history_sync.report import BatchRejected, Report
from envcite_ontology.import_problems import Problem
from envcite_ontology.ingest import audit, law
from envcite_ontology.ingest.backfill import anchors, planner, writer
from envcite_ontology.ingest.backfill.models import Anchor, Plan, UnreadablePage

log = logging.getLogger(__name__)


#: 資料庫以約束或型別擋下的寫入：資料本身不合，重試得到同樣的結果
_REJECTED_BY_DATABASE = (psycopg.errors.IntegrityError, psycopg.errors.DataError)


@dataclass
class _Prepared:
    item_id: int
    snapshot_id: int
    anchor: Anchor | None
    plan: Plan


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
            anchor, plan = _plan_item(conn, source, path.read_text())
        except Skip:
            report.skipped += 1
            continue
        for parsed in plan.parsed:
            metrics.add(parsed)
        prepared.append(_Prepared(item.item_id, item.snapshot_id, anchor, plan))
    return prepared


def _plan_item(conn: Conn, source: HistorySource, snapshot: str) -> tuple[Anchor | None, Plan]:
    """一部法規的回推計畫；對不到語料或頁面讀不出來的，計畫是空的、只帶 error 問題。

    來源判定不屬範圍的（`Skip`）往外拋。
    """
    problems: list[Problem] = []
    try:
        pcode, current, past = source.read(conn, snapshot)
    except UnreadablePage as e:
        problems.append(Problem("error", "unreadable", str(e)))
    except Unmatched as e:
        problems.append(Problem("error", e.code, e.detail))
    else:
        anchor = anchors.load_anchor(conn, pcode)
        if anchor is not None:
            return anchor, planner.plan(anchor, current, past)
        problems.append(Problem("error", "unmatched", pcode))
    return None, Plan(problems=problems)


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
    """一部一個交易；回傳動作（`writer.write` 的回傳，或沒有寫的 `rejected`）。

    延遲的約束在提交時才檢查，所以提交也在 `try` 之內。
    """
    problems = list(p.plan.problems)
    action = "rejected"
    with conn.transaction():
        if p.anchor is not None and p.plan.ok:
            action = writer.write(conn, p.anchor, p.plan.versions, source.authority)
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
