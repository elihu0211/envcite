"""`envcite-guidance sync <來源>`：取得行政規則並寫入 `guidance`，一件一個交易，可重跑。

兩個來源（`gazette` 行政院公報、`moenv` 環境部函釋），取得形狀不同、步驟相同：

1. **列舉**：讀清單頁，得出這次要處理的工作項（公報的一包、函釋的一列）。已處理過的略過
   （公報看包名、函釋看字號是否已有來源項目）；`--rescan` 全部重做，用來發現網站內容的改變。
2. **逐項**（`envcite_jobkit.run`：端點錯誤退避重試、斷路器、單項隔離）：下載、存成內容定址的快照，
   每件公文經 `guidance.load` 寫入（同一件內容相同即沿用，不同則記匯入問題、不改寫已載入的公文），
   最後在一個交易內記快照、來源項目與匯入問題（`source.import_problem`）。中途失敗，重跑時已寫入的
   公文沿用，未完成的項目重做。
3. **覆蓋率**：隔離的項目超過列舉數的 1% 時，本次判定失敗（02-01 §7.1）。
4. **停止適用**（逐項之後，各一個交易）：
   - 公報：主旨寫「廢止「甲」」或「停止適用「甲」」的一件，停止此前以「訂定」「修正」（含改名）
     刊登甲的每一件，自主旨寫明的生效日起，沒寫為發文日。名稱只在公報的行政規則之間比對；對到的
     出自兩個以上機關、或一件都沒對到，記匯入問題，不停止。只廢止其中幾點的不算。每次對全部已載入的
     公報行政規則重算，所以先刊登的廢止在回填出舊規則之後也會補上。
   - 函釋：不給 `--since` 的走訪是全量（02-05 §2）。全部葉分類走完、且沒出現的不超過已知函釋的
     5% 時，這次走訪才算完整；連續兩次完整走訪都沒出現的函釋記下架日，並自下架日起停止適用，
     公文不刪除。下架後又出現的記匯入問題，已寫入的停止適用不撤回，由人判斷。

錨定（`guidance_anchor`）：函釋只取「相關法規」欄；公報的行政規則沒有這一欄，只取內文寫出依據的
「核釋」「依」「為執行」之後的「法名第幾條」。法名以語料任一版本的標題或沿革所載的舊名解析且須唯一，
條號對到條文單元，錨定到該單元之下每個最細單元在**發文日**現行的文字；發文日沒有現行版本的不錨定，
不以現行文字代替。對不上的逐筆記成匯入問題，比例可列舉。

結束碼沿用 jobkit：0 完成；75 斷路器開啟；2 資料不合預期；1 其他暫時性失敗（含覆蓋率不足）。
"""

import argparse
import hashlib
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
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
    run,
)
from envcite_ontology import guidance, import_problems
from envcite_ontology.acquire import gazette, moenv
from envcite_ontology.acquire.engine import InvalidSnapshot
from envcite_ontology.acquire.web import Http
from envcite_ontology.db import Conn
from envcite_ontology.guidance_anchor import Resolver
from envcite_ontology.import_problems import Problem
from envcite_ontology.settings import DatabaseSettings, SnapshotSettings
from envcite_ontology.settings import load as load_settings

log = logging.getLogger(__name__)


EXIT_INVALID = 2
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
PRODUCER = "envcite-guidance-sync"
#: 改變寫入的公文內容、錨定或停止適用的規則時遞增
RULE_VERSION = "2"


class Client(Protocol):
    def text(self, url: str) -> str: ...

    def file(self, url: str, dest: Path) -> tuple[bytes, int]:
        """下載到 `dest`；回傳（SHA-256，位元組數）。"""
        ...


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
    #: 函釋：這次是否為完整的全量走訪；不是全量走訪為 None
    crawl_complete: bool | None = None
    #: 函釋：這次推論下架的件數
    dropped: int = 0
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
    client = client or Http(PAUSE_SECONDS)
    report = Report(source)
    resolver = Resolver(conn)
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
        code = _covered(report, stats, code)
        _repeals(conn, report)
    elif source == "moenv":
        crawl = _moenv_rows(conn, client, policy, sleep, since=since, rescan=rescan)
        report.listed = len(crawl.rows)
        code = run(
            crawl.rows,
            lambda r: job.letter(client, r),
            policy,
            item_id=lambda r: r.site_id,
            stats=stats,
            sleep=sleep,
        )
        code = _covered(report, stats, code)
        if since is None and code == ExitCode.OK:
            _crawled(conn, crawl, report)
    else:
        raise ValueError(f"unknown guidance source: {source}")
    report.exit_code = int(code)
    report.seconds = round(time.monotonic() - started, 1)
    log.info("行政規則取得：%s", report.line())
    return report


def _covered(report: Report, stats: RunStats, code: ExitCode) -> ExitCode:
    """隔離的項目超過列舉數的 1%，本次判定失敗（02-01 §7.1）。"""
    report.processed, report.dead_lettered = stats.processed, stats.dead_lettered
    if code == ExitCode.OK and report.dead_lettered > COVERAGE_TOLERANCE * report.listed:
        log.error("覆蓋率不足：%d／%d 項被隔離", report.dead_lettered, report.listed)
        return ExitCode.FAILED
    return code


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
) -> _Crawl:
    """走全部葉分類；清單依發文日新到舊，整頁都早於 `since` 就不再往後翻。"""

    def get(url: str) -> str:
        return _retrying(lambda: client.text(url), policy, sleep)

    leaves: list[str] = []
    for parent in moenv.parents(get(moenv.QUERY)):
        leaves += moenv.leaves(get(moenv.LIST.format(feid=parent, page=1))) or [parent]
    found: dict[str, moenv.Row] = {}
    complete = True
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
            complete = False
    done = set() if rescan else _keys(conn, "moenv_guidance")
    return _Crawl(
        rows=[r for r in found.values() if r.document_no not in done],
        seen=frozenset(r.document_no for r in found.values()),
        complete=complete,
    )


@dataclass(frozen=True)
class _Crawl:
    #: 這次要處理的列
    rows: Sequence[moenv.Row]
    #: 清單上出現的每個字號（含已處理過而略過的）
    seen: frozenset[str]
    #: 每個葉分類都走到了空頁
    complete: bool


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


def _crawled(conn: Conn, crawl: _Crawl, report: Report) -> None:
    """一次全量走訪之後：未出現的計數與下架由 `guidance.settle_crawl` 轉移（門檻在遷移 0050）。"""
    if not crawl.complete:
        report.crawl_complete = False
        log.warning("有葉分類沒走完，不推論下架")
        return
    with conn.transaction():
        row = conn.execute(
            "SELECT * FROM guidance.settle_crawl(%s, %s, %s)",
            (sorted(crawl.seen), date.today(), moenv.BASE),
        ).fetchone()
        assert row is not None
        complete, dropped, ceased, reappeared = row
        back = conn.execute(
            "SELECT id, last_snapshot_id FROM source.item WHERE id = ANY(%s)", (reappeared,)
        ).fetchall()
        for item_id, snapshot_id in back:
            _problems(
                conn,
                report,
                cast(int, item_id),
                cast(int, snapshot_id),
                [Problem("warning", "reappeared", "")],
            )
    report.crawl_complete = bool(complete)
    if not complete:
        log.warning("沒出現的函釋超過還在架上的 5%%，視為網站異常，不推論下架")
    report.dropped += cast(int, dropped)
    report.cessations += cast(int, ceased)


def _repeals(conn: Conn, report: Report) -> None:
    """公報的「廢止「甲」」停止此前刊登甲的各件（只在公報的行政規則之間比對名稱）。"""
    docs = conn.execute(
        "SELECT d.id, d.issuer, d.issued_on, d.subject,"
        "       EXISTS (SELECT 1 FROM guidance.cessation AS c WHERE c.document_id = d.id)"
        "  FROM guidance.document AS d WHERE starts_with(d.source_url, %s)"
        " ORDER BY d.issued_on, d.id",
        (gazette.BASE,),
    ).fetchall()
    conn.rollback()
    actions = {cast(int, r[0]): gazette.actions(str(r[3])) for r in docs}
    issuer = {cast(int, r[0]): str(r[1]) for r in docs}
    issued = {cast(int, r[0]): cast(date, r[2]) for r in docs}
    ceased = {cast(int, r[0]) for r in docs if r[4]}
    rules = _RuleNames()
    for doc_id, a in actions.items():
        rules.add(doc_id, a.sets, a.renames)
    for doc_id, a in actions.items():
        if not a.repeals:
            continue
        problems: list[Problem] = []
        earlier: set[int] = set()
        for name in a.repeals:
            found = {
                d for d in rules.documents(name) if issued[d] <= issued[doc_id] and d != doc_id
            }
            if not found:
                problems.append(Problem("warning", "repeal_unmatched", name))
            elif len({issuer[d] for d in found}) > 1:
                problems.append(Problem("warning", "repeal_ambiguous", name))
            else:
                earlier |= found
        on = a.effective_on
        if on is not None and on < issued[doc_id]:
            problems.append(Problem("warning", "repeal_before_issue", on.isoformat()))
            on = None
        pending = sorted(earlier - ceased)
        if pending:
            document = {
                **_loaded(conn, doc_id),
                "supersedes": [
                    {**_ref(conn, d), **({"from": on.isoformat()} if on else {})} for d in pending
                ],
            }
            loaded = _reload(conn, report, document)
            if loaded < 0:
                problems.append(Problem("warning", "repeal_rejected", "、".join(a.repeals)))
            else:
                # 同一條規則再被後一件廢止時，已停止的不再寫（停止適用只記最早的一次）
                ceased.update(pending)
                report.cessations += loaded
        _repeal_problems(conn, report, doc_id, problems)


class _RuleNames:
    """公報上的規則名稱 → 刊登它的各件。

    同一件裡的改名（「名稱並修正為」）把新舊名稱連成一條規則。
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._documents: dict[str, set[int]] = {}

    def add(self, doc_id: int, names: Sequence[str], renames: Sequence[tuple[str, str]]) -> None:
        for name in names:
            self._documents.setdefault(self._root(name), set()).add(doc_id)
        for old, new in renames:
            self._union(old, new)

    def documents(self, name: str) -> set[int]:
        return set(self._documents.get(self._root(name), set()))

    def _root(self, name: str) -> str:
        self._parent.setdefault(name, name)
        while self._parent[name] != name:
            name = self._parent[name]
        return name

    def _union(self, a: str, b: str) -> None:
        ra, rb = self._root(a), self._root(b)
        if ra != rb:
            self._parent[rb] = ra
            self._documents.setdefault(ra, set()).update(self._documents.pop(rb, set()))


def _loaded(conn: Conn, document_id: int) -> dict[str, Any]:
    """已載入的一件公文，照原樣：重新載入時是同一件，只多停止適用。"""
    row = conn.execute(
        "SELECT issuer, document_no, issued_on, rule_kind::text, subject, body, published_in,"
        "       source_url FROM guidance.document WHERE id = %s",
        (document_id,),
    ).fetchone()
    conn.rollback()
    assert row is not None
    keys = ("issuer", "document_no", "issued_on", "kind", "subject", "body")
    document: dict[str, Any] = dict(zip(keys, row[:6], strict=True))
    document["issued_on"] = cast(date, row[2]).isoformat()
    return document


def _ref(conn: Conn, document_id: int) -> dict[str, str]:
    d = _loaded(conn, document_id)
    return {"issuer": d["issuer"], "document_no": d["document_no"]}


def _reload(conn: Conn, report: Report, document: Mapping[str, Any]) -> int:
    """以 `guidance.load` 對已載入的公文補停止適用；回傳寫入的筆數，被拒為 -1。"""
    try:
        loaded = guidance.load(
            conn,
            {
                "derivation": {"method": "rule", "agent": PRODUCER, "agent_version": RULE_VERSION},
                "documents": [document],
            },
        )
    except guidance.GuidanceError as e:
        log.warning("停止適用未寫入：%s", e)
        return -1
    return loaded.cessations


def _repeal_problems(conn: Conn, report: Report, document_id: int, problems: list[Problem]) -> None:
    """廢止的對應問題記在該件公報刊登的來源項目上；每次重算，所以先清掉上一次的。"""
    with conn.transaction():
        row = conn.execute(
            "SELECT i.id, i.last_snapshot_id FROM guidance.document AS d"
            "  JOIN source.item AS i ON i.feed = 'gazette'"
            "   AND i.key = substring(d.source_url FROM 'metaid=([^&]+)')"
            " WHERE d.id = %s",
            (document_id,),
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "DELETE FROM source.import_problem WHERE item_id = %s AND problem_code = ANY(%s)",
            (row[0], list(REPEAL_PROBLEMS)),
        )
        _problems(conn, report, cast(int, row[0]), cast(int, row[1]), problems)


REPEAL_PROBLEMS = ("repeal_unmatched", "repeal_ambiguous", "repeal_before_issue", "repeal_rejected")


def _problems(
    conn: Conn, report: Report, item_id: int, snapshot_id: int, problems: Sequence[Problem]
) -> None:
    report.problems.update(p.code for p in problems)
    import_problems.record(conn, item_id, snapshot_id, problems)


class _Job:
    def __init__(self, conn: Conn, root: Path, report: Report, resolver: Resolver) -> None:
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
            return [Problem("error", "kind_ambiguous", r.title)]
        if r.issued_on is None or not r.body or not r.document_no or not r.issuer:
            return [Problem("error", "incomplete", r.title)]
        problems: list[Problem] = []
        interprets: list[dict[str, str]] = []
        for cited, citation in self._resolver.basis(r.body):
            found, missing = self._resolver.anchors(cited, r.issued_on, citation)
            interprets += found
            problems += missing
        document = {
            "issuer": r.issuer,
            "document_no": moenv.normalize_document_no(r.document_no),
            "issued_on": r.issued_on.isoformat(),
            "kind": kind,
            "subject": r.subject or r.title,
            "body": r.body,
            "published_in": r.published_in,
            "source_url": r.source_url,
            "interprets": interprets,
        }
        return problems + self._load(document)

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
            for cited in (c for related in letter.related for c in moenv.expand(related)):
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
                problems.append(Problem("warning", "ceased_without_date", row.url))
            elif row.ceased_on is not None and row.ceased_on < letter.issued_on:
                problems.append(Problem("warning", "ceased_before_issue", f"{row.ceased_on}"))
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
            return [Problem("warning", "content_changed", str(e))]
        except guidance.GuidanceError as e:
            return [Problem("error", "rejected", str(e))]
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
                conn.execute(
                    "DELETE FROM source.import_problem"
                    " WHERE item_id = %s AND NOT problem_code = ANY(%s)",
                    (item[0], list(REPEAL_PROBLEMS)),
                )
                _problems(conn, self._report, cast(int, item[0]), cast(int, snapshot_id), problems)


def _digest(rule: gazette.Rule) -> bytes:
    canonical = json.dumps(asdict(rule), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).digest()


def main() -> None:
    """`envcite-guidance`：`load` 載入人寫的文件，`sync` 自公報或環境部取得。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-guidance")
    commands = parser.add_subparsers(dest="command", required=True)
    load_cmd = commands.add_parser("load", help="載入一份人寫的行政規則文件（JSON）")
    load_cmd.add_argument("file", type=Path)
    sync_cmd = commands.add_parser("sync", help="自公報或環境部函釋取得行政規則並寫入")
    sync_cmd.add_argument("source", choices=("gazette", "moenv"))
    sync_cmd.add_argument(
        "--since", type=date.fromisoformat, help="只取此日（含）以後的刊登或發文；YYYY-MM-DD"
    )
    sync_cmd.add_argument(
        "--rescan", action="store_true", help="已處理過的也重抓，用來發現網站內容的改變"
    )
    args = parser.parse_args()

    if args.command == "sync":
        raise SystemExit(_run(args.source, since=args.since, rescan=args.rescan))
    settings = load_settings(DatabaseSettings)
    try:
        doc = json.loads(args.file.read_text(encoding="utf-8"))
        with psycopg.connect(settings.dsn) as conn:
            guidance.load(conn, doc)
    except (guidance.GuidanceError, json.JSONDecodeError) as e:
        log.error("guidance rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e


def _run(source: str, *, since: date | None, rescan: bool) -> int:
    settings = load_settings(SnapshotSettings)
    policy = Policy.load(defaults=POLICY)
    with psycopg.connect(settings.dsn) as conn:
        report = sync(
            conn, source, settings.snapshot_dir, since=since, rescan=rescan, policy=policy
        )
    return report.exit_code
