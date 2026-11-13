"""自憲法法庭網站取得違憲失效裁判（A5）。可重跑、冪等，網路只在第一步。

1. **掃描**：走解釋清單與判決清單，逐則抓頁、解析；主文宣告失效的裁判連同原文與出處入庫
   （`ruling.load`，同一字號內容不同即拒絕：裁判不可改）。每一頁的內容雜湊與抽取規則版本記在
   `corpus.ruling_scan`，記過的頁面不再抓；`--rescan` 無視紀錄全部重抓。
2. **抽取**：對庫裡**每一則**裁判的原文重跑 `ruling_extract`，把能確定的條文與失效日寫成
   `ruling_invalidation`。這一步只讀資料庫，所以語料新增法規（原本對不到標題的裁判）或規則改版後
   重跑就會補上，不必重抓網頁。抽取流程只刪改自己寫的列（`extractor_version` 有值）；人載入的
   列（`envcite-ruling load`）不動。

來源與授權見 `acquire/cons.py`；顯示這些資料時須標示出處（憲法法庭）。
"""

import argparse
import hashlib
import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg

from envcite_jobkit import Policy
from envcite_ontology import ruling
from envcite_ontology.acquire import cons
from envcite_ontology.acquire.engine import download as download_file
from envcite_ontology.db import Conn
from envcite_ontology.ruling_extract import EXTRACTOR_VERSION, extract, should_keep
from envcite_ontology.settings import DatabaseSettings
from envcite_ontology.settings import load as load_settings

log = logging.getLogger(__name__)

type Fetch = Callable[[str], str]

#: 對來源網站的禮貌間隔（秒）；同一次執行逐頁循序抓。
PAUSE_SECONDS = 0.5

EXIT_TRANSIENT = 1
EXIT_INVALID = 2


@dataclass(frozen=True)
class SyncResult:
    """一次 `envcite-ruling fetch` 的計數。"""

    #: 這次抓了幾頁
    fetched: int
    #: 主文宣告失效而入庫的裁判（含先前已入庫者）
    rulings: int
    #: 抽取流程目前寫著的失效條文單元數
    invalidations: int
    #: 入庫了、但規則還提不出條文的裁判數：原文在庫，等規則或語料補上
    unresolved: int
    #: 抓到了卻不是解釋或判決頁的數目
    unreadable: int


def download(url: str, policy: Policy | None = None) -> str:
    """下載一頁（端點問題依 jobkit 政策重試）。`file://` 也可，供測試。"""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "page"
        download_file(url, dest, cons.tls(), policy or Policy())
        return dest.read_text(encoding="utf-8-sig")


def sync(
    conn: Conn,
    *,
    fetch: Fetch = download,
    rescan: bool = False,
    interpretation_list: str = cons.INTERPRETATION_LIST,
    judgment_list: str = cons.JUDGMENT_LIST,
    base: str = cons.BASE,
    pause: float = PAUSE_SECONDS,
) -> SyncResult:
    """掃描憲法法庭的裁判並入庫，再對庫裡每則裁判重抽失效條文。

    只入庫主文宣告失效的裁判。已掃描過（記錄的抽取規則版本等於現行）的頁面略過，`rescan`
    時全抓；抓到卻不是解釋或判決頁的只計入 `unreadable`，不中斷。網路只在掃描這一步。
    """
    fetched = unreadable = 0
    for url in _urls(fetch, interpretation_list, judgment_list, base, pause):
        if not rescan and _scanned(conn, url):
            continue
        if fetched:
            time.sleep(pause)
        fetched += 1
        try:
            page = cons.parse(fetch(url), url)
        except cons.UnreadablePage as e:
            log.error("unreadable page: %s", e)
            unreadable += 1
            continue
        _scan(conn, page)
    rulings, invalidations, unresolved = _extract(conn)
    result = SyncResult(fetched, rulings, invalidations, unresolved, unreadable)
    log.info("違憲失效取得：%s", result)
    return result


def _urls(
    fetch: Fetch, interpretation_list: str, judgment_list: str, base: str, pause: float
) -> list[str]:
    first = fetch(interpretation_list.format(page=1))
    urls = cons.document_links(first, base)
    for n in range(2, cons.last_page(first) + 1):
        time.sleep(pause)
        urls += cons.document_links(fetch(interpretation_list.format(page=n)), base)
    urls += cons.document_links(fetch(judgment_list), base)
    return list(dict.fromkeys(urls))


def _scanned(conn: Conn, url: str) -> bool:
    row = conn.execute(
        "SELECT extractor_version FROM corpus.ruling_scan WHERE source_url = %s", (url,)
    ).fetchone()
    conn.rollback()
    return row is not None and row[0] == EXTRACTOR_VERSION


def _scan(conn: Conn, page: cons.RulingPage) -> None:
    digest = hashlib.sha256(
        f"{page.ruling_no}\n{page.decided_on}\n{page.main_text}".encode()
    ).digest()
    if should_keep(page.main_text):
        ruling.load(
            conn,
            {
                "rulings": [
                    {
                        "ruling_no": page.ruling_no,
                        "kind": page.kind,
                        "decided_on": page.decided_on.isoformat(),
                        "source_url": page.source_url,
                        "excerpt": page.main_text,
                        "invalidates": [],
                    }
                ]
            },
        )
    conn.execute(
        "INSERT INTO corpus.ruling_scan (source_url, content_sha256, extractor_version)"
        " VALUES (%s, %s, %s)"
        " ON CONFLICT (source_url) DO UPDATE SET content_sha256 = excluded.content_sha256,"
        "   extractor_version = excluded.extractor_version, scanned_at = now()",
        (page.source_url, digest, EXTRACTOR_VERSION),
    )
    conn.commit()


def _extract(conn: Conn) -> tuple[int, int, int]:
    """對庫裡每則裁判重抽；一個交易，整批成功或整批不變。"""
    titles = _titles(conn)
    try:
        rows = conn.execute(
            "SELECT id, decided_on, excerpt FROM corpus.constitutional_ruling ORDER BY id"
        ).fetchall()
        unresolved = 0
        for ruling_id, decided_on, excerpt in rows:
            conn.execute(
                "DELETE FROM corpus.ruling_invalidation"
                " WHERE ruling_id = %s AND extractor_version IS NOT NULL",
                (ruling_id,),
            )
            written = 0
            for c in extract(str(excerpt), decided_on, frozenset(titles)):  # type: ignore[arg-type]
                pcode = titles[c.law_title]
                if pcode is None or c.invalid_from < decided_on:  # type: ignore[operator]
                    continue
                written += conn.execute(
                    "INSERT INTO corpus.ruling_invalidation"
                    " (ruling_id, resource_id, provision_id, invalid_from, partial,"
                    "  extractor_version)"
                    " SELECT %s, p.resource_id, p.id, %s, %s, %s FROM corpus.provision AS p"
                    "   JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
                    "  WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s"
                    " ON CONFLICT (ruling_id, provision_id) DO NOTHING",
                    (ruling_id, c.invalid_from, c.partial, EXTRACTOR_VERSION, pcode, c.path),
                ).rowcount
            has_any = conn.execute(
                "SELECT 1 FROM corpus.ruling_invalidation WHERE ruling_id = %s", (ruling_id,)
            ).fetchone()
            unresolved += has_any is None
        total = conn.execute(
            "SELECT count(*) FROM corpus.ruling_invalidation WHERE extractor_version IS NOT NULL"
        ).fetchone()
        assert total is not None
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return len(rows), int(total[0]), unresolved  # type: ignore[call-overload]


def _titles(conn: Conn) -> dict[str, str | None]:
    """標題 → pcode；同一標題對到兩部法規（歷來名稱重複）為 None，抽取時略過。"""
    found: dict[str, str | None] = {}
    for title, pcode in conn.execute(
        "SELECT DISTINCT e.title, r.local_id FROM corpus.legal_expression AS e"
        "  JOIN corpus.legal_resource AS r ON r.id = e.resource_id"
        " WHERE r.id_scheme = 'moj_pcode' AND e.language = 'zho' AND char_length(e.title) >= 2"
    ):
        key = str(title)
        found[key] = str(pcode) if key not in found or found[key] == pcode else None
    conn.rollback()
    return found


def main() -> None:
    """`envcite-ruling`：`load` 載入人寫的文件，`fetch` 自憲法法庭網站取得。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-ruling")
    commands = parser.add_subparsers(dest="command", required=True)
    load_cmd = commands.add_parser("load", help="載入違憲失效裁判文件（JSON）")
    load_cmd.add_argument("file", type=Path)
    fetch_cmd = commands.add_parser("fetch", help="自憲法法庭網站取得裁判並提出失效")
    fetch_cmd.add_argument(
        "--rescan", action="store_true", help="已掃描過的頁面也重抓（網站內容有疑義時）"
    )
    args = parser.parse_args()

    settings = load_settings(DatabaseSettings)
    try:
        with psycopg.connect(settings.dsn) as conn:
            if args.command == "load":
                ruling.load(conn, json.loads(args.file.read_text(encoding="utf-8")))
            else:
                raise SystemExit(_fetch(conn, rescan=args.rescan))
    except (ruling.RulingError, json.JSONDecodeError) as e:
        log.error("ruling rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e


def _fetch(conn: Conn, *, rescan: bool) -> int:
    try:
        result = sync(conn, rescan=rescan)
    except OSError as e:
        log.error("ruling fetch failed: %s", e)
        return EXIT_TRANSIENT
    return EXIT_INVALID if result.unreadable else 0
