"""`envcite-guidance sync <來源>`：取得行政規則並寫入 `guidance`，一件一個交易，可重跑。

兩個來源（`gazette` 行政院公報、`moenv` 環境部函釋），取得形狀不同、步驟相同：

1. **列舉**：讀清單頁，得出這次要處理的工作項（公報的一包、函釋的一列）。已處理過的略過
   （公報看包名、函釋看字號是否已有來源項目）；`--rescan` 全部重做，用來發現網站內容的改變。
2. **逐項**（`envcite_jobkit.run`：端點錯誤退避重試、斷路器、單項隔離）：下載、存成內容定址的快照，
   每件公文經 `guidance.load` 寫入（同一件內容相同即沿用，不同則記匯入問題、不改寫已載入的公文），
   最後在一個交易內記快照、來源項目與匯入問題（`source.import_problem`）。中途失敗，重跑時已寫入的
   公文沿用，未完成的項目重做。
3. **覆蓋率**：隔離的項目超過列舉數的 1% 時，本次判定失敗（02-01 §7.1）。

函釋解釋的條文只取「相關法規」欄（02-05 §7.2）：法名對語料任一版本的標題且唯一，條號對到條文單元，
錨定到該單元之下每個最細單元在**發文日**現行的文字；發文日沒有現行版本的不錨定，不以現行文字代替。
公報的行政規則沒有相關法規欄，不錨定。對不上的逐筆記成匯入問題，比例可列舉。

結束碼沿用 jobkit：0 完成；75 斷路器開啟；2 資料不合預期；1 其他暫時性失敗（含覆蓋率不足）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import ssl
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from http.client import HTTPException
from pathlib import Path
from typing import Any, Protocol, cast

import psycopg

from envcite_jobkit import (
    EndpointError,
    ExitCode,
    ItemError,
    Policy,
    RunStats,
    backoff_delay,
    raise_for_status,
    run,
)
from envcite_ontology import guidance
from envcite_ontology.acquire import gazette, moenv
from envcite_ontology.acquire.engine import InvalidSnapshot

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]
POLICY = Policy(
    name="guidance",
    open_action="exit",
    max_attempts=4,
    backoff_base_seconds=2.0,
    backoff_max_seconds=60.0,
)
#: 對來源網站的禮貌間隔（秒）；逐頁循序抓（02-01 表 crawl-params）
PAUSE_SECONDS = 0.5
#: 隔離的項目占列舉數超過此比例，本次判定失敗
COVERAGE_TOLERANCE = 0.01
#: 一個葉分類最多翻幾頁；觸及時告警（02-01 §7.1）
MAX_PAGES = 200
#: 連續幾頁沒有資料就判定葉分類走完（網站的總筆數不可信）
EMPTY_PAGES = 3
USER_AGENT = "envcite-ontology (+https://gitlab.com/thuaisse-lab/envcite)"
PRODUCER = "envcite-guidance-sync"
#: 改變寫入的公文內容或錨定規則時遞增
RULE_VERSION = "1"

Problem = tuple[str, str, str]  # （severity，problem_code，detail）


class Client(Protocol):
    def text(self, url: str) -> str: ...

    def file(self, url: str, dest: Path) -> tuple[bytes, int]:
        """下載到 `dest`；回傳（SHA-256，位元組數）。"""
        ...


class Http:
    """循序、帶禮貌間隔與身分的 HTTP。失敗依 jobkit 分類：端點問題可重試，其餘隔離或致命。"""

    def __init__(self, pause: float = PAUSE_SECONDS) -> None:
        self._pause = pause
        self._last = 0.0
        # 兩站憑證都缺少 Subject Key Identifier（同全國法規資料庫），只清除這一個旗標
        self._tls = ssl.create_default_context()
        self._tls.verify_flags &= ~ssl.VERIFY_X509_STRICT

    def text(self, url: str) -> str:
        with self._open(url) as r:
            return cast(bytes, r.read()).decode("utf-8-sig")

    def file(self, url: str, dest: Path) -> tuple[bytes, int]:
        h = hashlib.sha256()
        size = 0
        with self._open(url) as r, dest.open("wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
                size += len(chunk)
        if size == 0:
            raise ItemError(f"{url}: empty response")
        return h.digest(), size

    def _open(self, url: str) -> Any:
        wait = self._last + self._pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(request, context=self._tls, timeout=60)
        except urllib.error.HTTPError as e:
            raise_for_status(e.code, f"{url}: {e}")
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, HTTPException) as e:
            raise EndpointError(f"{url}: {e}") from e


@dataclass
class Report:
    source: str
    listed: int = 0
    processed: int = 0
    dead_lettered: int = 0
    documents: int = 0
    #: 已載入過、內容相同而沿用的件數
    repeated: int = 0
    interpretations: int = 0
    cessations: int = 0
    problems: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0
    exit_code: int = 0

    def line(self) -> str:
        return json.dumps({**asdict(self), "problems": dict(self.problems)}, ensure_ascii=False)


@dataclass(frozen=True)
class _Captured:
    """一個工作項下載並寫入公文之後、記入取得紀錄之前的結果。"""

    feed: str
    data_date: date
    license_notice: str
    origin_name: str | None
    #: （來源內的鍵，內容的 SHA-256，匯入問題）
    items: Sequence[tuple[str, bytes, Sequence[Problem]]]


def sync(
    conn: Conn,
    source: str,
    root: Path,
    *,
    since: date | None = None,
    rescan: bool = False,
    client: Client | None = None,
    policy: Policy = POLICY,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    started = time.monotonic()
    client = client or Http()
    report = Report(source)
    resolver = _Resolver(conn)
    job = _Job(conn, root, report, resolver)
    stats = RunStats()
    if source == "gazette":
        packs = _gazette_packs(conn, client, policy, sleep, since=since, rescan=rescan)
        report.listed = len(packs)
        code = run(
            packs,
            lambda p: job.pack(client, p),
            policy,
            item_id=lambda p: p.name,
            stats=stats,
            sleep=sleep,
        )
    elif source == "moenv":
        rows = _moenv_rows(conn, client, policy, sleep, since=since, rescan=rescan)
        report.listed = len(rows)
        code = run(
            rows,
            lambda r: job.letter(client, r),
            policy,
            item_id=lambda r: r.site_id,
            stats=stats,
            sleep=sleep,
        )
    else:
        raise ValueError(f"unknown guidance source: {source}")
    report.processed, report.dead_lettered = stats.processed, stats.dead_lettered
    if code == ExitCode.OK and report.dead_lettered > COVERAGE_TOLERANCE * report.listed:
        log.error("覆蓋率不足：%d／%d 項被隔離", report.dead_lettered, report.listed)
        code = ExitCode.FAILED
    report.exit_code = int(code)
    report.seconds = round(time.monotonic() - started, 1)
    log.info("行政規則取得：%s", report.line())
    return report


def _retrying[T](fn: Callable[[], T], policy: Policy, sleep: Callable[[float], None]) -> T:
    """清單頁不是工作項：端點問題就地退避重試，用盡則拋出，整次失敗（結束碼 1）。"""
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except EndpointError as e:
            if attempt == policy.max_attempts:
                raise
            delay = backoff_delay(
                attempt, base=policy.backoff_base_seconds, cap=policy.backoff_max_seconds
            )
            log.warning("%s，%.0f 秒後重試（第 %d 次）", e, delay, attempt)
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _gazette_packs(
    conn: Conn,
    client: Client,
    policy: Policy,
    sleep: Callable[[float], None],
    *,
    since: date | None,
    rescan: bool,
) -> list[gazette.Pack]:
    """本月的每日包與 `since` 起的歷史月包；未給 `since` 時從上個月一日起。"""
    today = date.today()
    if since is None:
        since = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    listings = [gazette.MONTH_LIST] + [
        gazette.YEAR_LIST.format(year=year - 1911) for year in range(since.year, today.year + 1)
    ]
    found: dict[str, gazette.Pack] = {}
    for url in listings:
        listing = _retrying(lambda: client.text(url), policy, sleep)  # noqa: B023
        for p in gazette.packs(listing):
            if p.covers_from(since):
                found.setdefault(p.name, p)
    done = set() if rescan else _origins(conn, "gazette")
    return sorted((p for p in found.values() if p.name not in done), key=lambda p: p.name)


def _moenv_rows(
    conn: Conn,
    client: Client,
    policy: Policy,
    sleep: Callable[[float], None],
    *,
    since: date | None,
    rescan: bool,
) -> list[moenv.Row]:
    """走全部葉分類；清單依發文日新到舊，整頁都早於 `since` 就不再往後翻。"""

    def get(url: str) -> str:
        return _retrying(lambda: client.text(url), policy, sleep)

    leaves: list[str] = []
    for parent in moenv.parents(get(moenv.QUERY)):
        leaves += moenv.leaves(get(moenv.LIST.format(feid=parent, page=1))) or [parent]
    found: dict[str, moenv.Row] = {}
    for leaf in dict.fromkeys(leaves):
        empty = 0
        for page in range(1, MAX_PAGES + 1):
            rows = moenv.rows(get(moenv.LIST.format(feid=leaf, page=page)))
            if not rows:
                empty += 1
                if empty == EMPTY_PAGES:
                    break
                continue
            empty = 0
            for r in rows:
                if since is None or r.listed_on >= since:
                    found.setdefault(r.site_id, r)
            if since is not None and all(r.listed_on < since for r in rows):
                break
        else:
            log.warning("葉分類 %s 翻到 %d 頁仍有資料，未走完", leaf, MAX_PAGES)
    done = set() if rescan else _keys(conn, "moenv_guidance")
    return [r for r in found.values() if r.document_no not in done]


def _origins(conn: Conn, feed: str) -> set[str]:
    rows = conn.execute(
        "SELECT origin_name FROM source.snapshot WHERE feed = %s AND origin_name IS NOT NULL",
        (feed,),
    ).fetchall()
    conn.rollback()
    return {str(r[0]) for r in rows}


def _keys(conn: Conn, feed: str) -> set[str]:
    rows = conn.execute("SELECT key FROM source.item WHERE feed = %s", (feed,)).fetchall()
    conn.rollback()
    return {str(r[0]) for r in rows}


class _Resolver:
    """法規名稱 → pcode（02-05 §3.3 的第一層：語料任一版本的標題）；同名對到數部為 None。"""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn
        titles: dict[str, str | None] = {}
        for title, pcode in conn.execute(
            "SELECT DISTINCT e.title, r.local_id FROM corpus.legal_expression AS e"
            "  JOIN corpus.legal_resource AS r ON r.id = e.resource_id"
            " WHERE r.id_scheme = 'moj_pcode'"
        ):
            key = _name(str(title))
            titles[key] = str(pcode) if titles.get(key, pcode) == pcode else None
        conn.rollback()
        self._titles = titles

    def anchors(self, cited: str, on: date) -> tuple[list[dict[str, str]], list[Problem]]:
        """一處「相關法規」→ 載入文件的 `interprets`，或對不上的原因。"""
        c = moenv.citation(cited)
        key = _name(c.law_name)
        if key not in self._titles:
            return [], [("warning", "law_unresolved", cited)]
        pcode = self._titles[key]
        if pcode is None:
            return [], [("warning", "law_ambiguous", cited)]
        if c.path is None:
            return [], [("warning", "no_article", cited)]
        rows = self._conn.execute(
            """
            SELECT p.path, v.content
              FROM corpus.provision AS p
              JOIN corpus.legal_resource AS r ON r.id = p.resource_id
              LEFT JOIN corpus.provision_version AS v
                ON v.provision_id = p.id AND v.superseded_at IS NULL AND v.content IS NOT NULL
               AND v.force_period @> %(on)s::date
             WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %(pcode)s
               AND (p.path = %(path)s OR starts_with(p.path, %(path)s || '/'))
             ORDER BY p.sort_key
            """,
            {"pcode": pcode, "path": c.path, "on": on},
        ).fetchall()
        if not rows:
            return [], [("warning", "provision_unresolved", cited)]
        found = [
            {"law": pcode, "path": str(path), "text": str(text)}
            for path, text in rows
            if text is not None
        ]
        if not found:
            return [], [("warning", "no_version_at_issue", f"{cited}（{on}）")]
        return found, []


def _name(title: str) -> str:
    """名稱比對只正規化全形半形與空白（02-05 §3.2）。"""
    return "".join(unicodedata.normalize("NFKC", title).split())


class _Job:
    def __init__(self, conn: Conn, root: Path, report: Report, resolver: _Resolver) -> None:
        self._conn = conn
        self._root = root
        self._report = report
        self._resolver = resolver
        self._derivation = {"method": "rule", "agent": PRODUCER, "agent_version": RULE_VERSION}

    def pack(self, client: Client, pack: gazette.Pack) -> None:
        partial = self._partial("gazette")
        try:
            digest, size = client.file(pack.url, partial)
            try:
                data_date, rules = gazette.read(partial)
            except InvalidSnapshot as e:
                raise ItemError(f"{pack.name}: {e}") from e
            items = [(r.meta_id, _digest(r), self._gazette_rule(r)) for r in rules]
            captured = _Captured("gazette", data_date, gazette.ATTRIBUTION, pack.name, items)
            self._record(captured, partial, digest, size)
        finally:
            partial.unlink(missing_ok=True)

    def _gazette_rule(self, r: gazette.Rule) -> list[Problem]:
        try:
            kind = gazette.rule_kind(r.title)
        except gazette.AmbiguousKind:
            return [("error", "kind_ambiguous", r.title)]
        if r.issued_on is None or not r.body or not r.document_no or not r.issuer:
            return [("error", "incomplete", r.title)]
        document = {
            "issuer": r.issuer,
            "document_no": moenv.normalize_document_no(r.document_no),
            "issued_on": r.issued_on.isoformat(),
            "kind": kind,
            "subject": r.subject or r.title,
            "body": r.body,
            "published_in": r.published_in,
            "source_url": r.source_url,
        }
        return self._load(document)

    def letter(self, client: Client, row: moenv.Row) -> None:
        try:
            fragment = moenv.content(client.text(row.url))
            letter = moenv.parse(fragment)
        except moenv.UnreadablePage as e:
            raise ItemError(f"{row.url}: {e}") from e
        partial = self._partial("moenv_guidance")
        try:
            data = fragment.encode()
            partial.write_bytes(data)
            problems: list[Problem] = []
            interprets: list[dict[str, str]] = []
            for cited in letter.related:
                found, missing = self._resolver.anchors(cited, letter.issued_on)
                interprets += found
                problems += missing
            document: dict[str, Any] = {
                "issuer": letter.issuer,
                "document_no": letter.document_no,
                "issued_on": letter.issued_on.isoformat(),
                "kind": "interpretation",
                "subject": letter.subject,
                "body": letter.body,
                "source_url": row.url,
                "interprets": interprets,
            }
            if row.ceased and row.ceased_on is None:
                problems.append(("warning", "ceased_without_date", row.url))
            elif row.ceased_on is not None and row.ceased_on < letter.issued_on:
                problems.append(("warning", "ceased_before_issue", f"{row.ceased_on}"))
            elif row.ceased_on is not None:
                document["ceased_on"] = row.ceased_on.isoformat()
            problems += self._load(document)
            item = (letter.document_no, hashlib.sha256(data).digest(), problems)
            captured = _Captured("moenv_guidance", date.today(), moenv.ATTRIBUTION, None, [item])
            self._record(captured, partial, hashlib.sha256(data).digest(), len(data))
        finally:
            partial.unlink(missing_ok=True)

    def _load(self, document: Mapping[str, Any]) -> list[Problem]:
        """一件公文一個交易；內容與已載入者不同不改寫，記成問題（公文不可改）。"""
        try:
            loaded = guidance.load(
                self._conn, {"derivation": self._derivation, "documents": [document]}
            )
        except guidance.DocumentChanged as e:
            return [("warning", "content_changed", str(e))]
        except guidance.GuidanceError as e:
            return [("error", "rejected", str(e))]
        self._report.documents += loaded.documents - loaded.repeated
        self._report.repeated += loaded.repeated
        self._report.interpretations += loaded.interpretations
        self._report.cessations += loaded.cessations
        return []

    def _partial(self, feed: str) -> Path:
        partial = self._root / ".partial" / feed
        partial.parent.mkdir(parents=True, exist_ok=True)
        return partial

    def _record(self, c: _Captured, partial: Path, digest: bytes, size: int) -> None:
        """快照、來源項目與匯入問題，一個交易。檔案先就位、資料庫後提交，檔名即雜湊。"""
        location = f"{c.feed}/{digest.hex()}"
        (self._root / location).parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, self._root / location)
        conn = self._conn
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO source.snapshot"
                " (feed, data_date, sha256, size_bytes, location, license_notice, origin_name)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (feed, sha256) DO UPDATE SET origin_name"
                "   = coalesce(source.snapshot.origin_name, excluded.origin_name)"
                " RETURNING id",
                (c.feed, c.data_date, digest, size, location, c.license_notice, c.origin_name),
            ).fetchone()
            assert row is not None
            snapshot_id = row[0]
            for key, sha, problems in c.items:
                item = conn.execute(
                    "INSERT INTO source.item"
                    " (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
                    " VALUES (%(feed)s, %(key)s, %(sha)s, %(id)s, %(id)s)"
                    " ON CONFLICT (feed, key) DO UPDATE"
                    "   SET content_sha256 = excluded.content_sha256,"
                    "       last_snapshot_id = excluded.last_snapshot_id"
                    " RETURNING id",
                    {"feed": c.feed, "key": key, "sha": sha, "id": snapshot_id},
                ).fetchone()
                assert item is not None
                conn.execute("DELETE FROM source.import_problem WHERE item_id = %s", (item[0],))
                for severity, code, detail in problems:
                    self._report.problems[code] += 1
                    conn.execute(
                        "INSERT INTO source.import_problem"
                        " (item_id, snapshot_id, severity, problem_code, detail)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (item[0], snapshot_id, severity, code, detail),
                    )


def _digest(rule: gazette.Rule) -> bytes:
    canonical = json.dumps(asdict(rule), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).digest()
