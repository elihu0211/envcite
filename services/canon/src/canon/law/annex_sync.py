"""`canon annexes`：環境部主管的命令的附表，取得並寫成附表單元。

目標是環境部主管法規查詢系統列出的命令（`Moenv.targets`），以（現名，種類）對到語料的法規；對不到或
同名數部的略過並記下。現行頁上的附表連結（`annex.links`）逐個下載、抽 PDF 文字層，一部法規一個交易
寫入（`annex.write`）。下載走 `Http` 的禮貌間隔；附件取自環境部系統，須註明出處
（`moenv_law.ATTRIBUTION`）。
"""

import argparse
import logging
import tempfile
from datetime import date
from pathlib import Path

import psycopg

from canon.acquire.web import Http
from canon.db import Conn
from canon.law import annex, moenv_law
from canon.law.history_sources import Moenv, Target, Web
from canon.settings import SnapshotSettings, load

log = logging.getLogger(__name__)

#: 隔離的法規超過這個比例，結束碼為失敗。
FAILURE_TOLERANCE = 0.01


def sync(
    conn: Conn,
    web: Http,
    *,
    only: set[str] | None = None,
    dry_run: bool = False,
    source: Moenv | None = None,
) -> int:
    """取得並寫入附表；回傳結束碼（0 成功，1 失敗的法規比例超限）。"""
    source = source or Moenv()
    listed = source.targets(conn, web)
    conn.rollback()
    named = source.named(conn)
    conn.rollback()
    counts = {"written": 0, "unchanged": 0, "no_annex": 0, "absent": 0, "unmatched": 0, "failed": 0}
    for target in listed:
        pcodes = _pcodes(named, target)
        if pcodes is None:
            counts["unmatched"] += 1
            continue
        pcode = pcodes
        if only is not None and pcode not in only:
            continue
        try:
            result = _one(conn, web, target, pcode, dry_run=dry_run)
        except Exception:
            conn.rollback()
            log.exception("附表失敗 %s（%s）", target.title, pcode)
            counts["failed"] += 1
            continue
        counts[result] += 1
    log.info("附表：%s", counts)
    return 1 if counts["failed"] > FAILURE_TOLERANCE * max(len(listed), 1) else 0


def _pcodes(named: dict[tuple[str, str], list[tuple[str, date]]], target: Target) -> str | None:
    found = named.get((target.title, target.kind), [])
    return found[0][0] if len(found) == 1 else None


def _one(conn: Conn, web: Web, target: Target, pcode: str, *, dry_run: bool) -> str:
    page = web.text(moenv_law.CURRENT.format(site_id=target.key))
    found = annex.links(page)
    if not found:
        return "no_annex"
    annexes: list[annex.Annex] = []
    complete = True
    for link in found:
        text = _download(web, link)
        if text is None:
            log.warning("附表沒有文字層或讀不出 %s %s", target.title, link.name)
            complete = False
            continue
        annexes.append(annex.Annex(link.path, text))
    if dry_run:
        return "unchanged"
    result = annex.write(conn, pcode, annexes, prune=complete)
    conn.commit()
    return result


def _download(web: Web, link: annex.Link) -> str | None:
    assert isinstance(web, Http)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "annex.pdf"
        web.file(moenv_law.BASE + link.url, path)
        return annex.extract_text(path.read_bytes())


def main() -> None:
    """`canon annexes [--law PCODE]… [--dry-run]`：取得環境部命令的附表並寫入語料。"""
    parser = argparse.ArgumentParser(prog="canon annexes")
    parser.add_argument("--law", action="append", default=[], help="只處理這個 pcode，可重複")
    parser.add_argument("--dry-run", action="store_true", help="只下載與抽字，不寫入")
    args = parser.parse_args()
    settings = load(SnapshotSettings)
    web = Http(Moenv.pause)
    with psycopg.connect(settings.dsn) as conn:
        raise SystemExit(sync(conn, web, only=set(args.law) or None, dry_run=args.dry_run))
