"""`envcite-history` 的來源：列出目標、抓一部法規的各版、對到語料的法規。

一部法規的歷史只取自一個來源，依種類分，兩個來源不會寫同一部：

- `ly`：**法律**，立法院法律系統（制定機關；解析與授權見 `acquire/ly_law.py`）。目標是語料裡現行的
  法律，以現名檢索；鍵是全國法規資料庫的法規代碼。寫成 `text_authority = legislature`。
- `moenv`：**命令**，環境部主管法規查詢系統（主管機關；見 `acquire/moenv_law.py`）。目標是走分類樹
  列出的命令，以名稱與種類對到語料；鍵是該系統的法規代碼。
  寫成 `text_authority = competent_authority`。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, cast

from envcite_ontology.acquire import ly_law, moenv_law
from envcite_ontology.db import Conn
from envcite_ontology.ingest.backfill import UnreadablePage, Version

_NORM_KINDS = {"法律": ("statute", "constitution"), "命令": ("regulation",)}


class Web(Protocol):
    """來源需要的網頁客戶端：GET 與 POST 表單（`acquire.web.Http`）。"""

    def text(self, url: str) -> str:
        """GET 一頁，回傳解碼後的文字。"""
        ...

    def form(self, url: str, fields: dict[str, str]) -> str:
        """POST 表單，回傳回應的文字。"""
        ...


@dataclass(frozen=True, slots=True)
class Target:
    """來源列出的、要取得歷史的一部法規。"""

    #: 來源內的法規代碼，是 `source.item` 的鍵。
    key: str
    title: str
    #: 種類（法律、命令）；以標題檢索的來源不用，為空字串。
    kind: str = ""


class Skip(Exception):
    """快照不屬這個來源現在的範圍（如舊快照的種類），不匯入也不記問題。"""


class Unmatched(Exception):
    """快照的法規對不到語料：`code` 是匯入問題代碼，`detail` 是指出是哪一部的人讀說明。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class HistorySource(Protocol):
    """一個歷史來源要實作的介面（`Ly`、`Moenv`），在 `SOURCES` 登記。

    `feed` 是快照的來源名稱，`authority` 是寫入歷史版本的 `text_authority`。
    """

    feed: str
    authority: str
    attribution: str
    #: 對來源網站的禮貌間隔（秒）；逐頁循序抓
    pause: float
    legacy_tls: bool
    #: 整批驗收：對不到語料法規的占列舉數上限
    unmatched_limit: float

    def targets(self, conn: Conn, web: Web) -> list[Target]:
        """列出這次要取得歷史的法規。"""
        ...

    def bundle(self, web: Web, target: Target, previous: dict[str, Any]) -> dict[str, Any]:
        """一部法規的快照內容。

        `versions` 是過去各版（鍵跨次不變）；`_fetched`、`_reused` 是頁數，不進快照。
        """
        ...

    def resolve(self, conn: Conn, bundle: dict[str, Any]) -> str:
        """語料的法規代碼；對不到拋 `Unmatched`，不屬範圍拋 `Skip`。"""
        ...

    def versions(self, bundle: dict[str, Any]) -> tuple[Version, list[Version]]:
        """快照內容解出（現行版本，過去各版）；頁面讀不出來拋 `UnreadablePage`。"""
        ...


class Moenv:
    """命令的歷史來源：環境部主管法規查詢系統（主管機關）。"""

    feed = "moenv_law"
    authority = "competent_authority"
    attribution = moenv_law.ATTRIBUTION
    pause = 1.0
    legacy_tls = False
    unmatched_limit = 0.02
    #: 法律的歷史取自立法院（`Ly`），這裡只收命令
    KINDS = ("命令",)

    def __init__(self) -> None:
        self._titles: dict[tuple[str, str], list[tuple[str, date]]] | None = None

    def targets(self, conn: Conn, web: Web) -> list[Target]:
        """分類樹的每個葉分類翻到底；同一部法規出現在數個分類只算一次。"""
        root = web.text(moenv_law.CATEGORIES)
        leaves = moenv_law.leaf_categories(root)
        for parent in moenv_law.parent_categories(root):
            leaves += moenv_law.leaf_categories(
                web.text(f"{moenv_law.CATEGORIES}&CategoryID={parent}")
            )
        found: dict[str, Target] = {}
        for leaf in dict.fromkeys(leaves):
            page = 1
            while True:
                listed = moenv_law.list_rows(
                    web.text(moenv_law.LIST.format(category=leaf, page=page))
                )
                for r in listed:
                    if r.kind in self.KINDS:
                        found.setdefault(r.site_id, Target(r.site_id, r.title, r.kind))
                if len(listed) < moenv_law.PAGE_SIZE:
                    break
                page += 1
        return list(found.values())

    def bundle(self, web: Web, target: Target, previous: dict[str, Any]) -> dict[str, Any]:
        """現行頁與歷史清單上每個 `hid` 的頁面內容；`previous` 已有的 `hid` 沿用不重抓。"""
        listing = web.text(moenv_law.HISTORY_LIST.format(site_id=target.key))
        current = web.text(moenv_law.CURRENT.format(site_id=target.key))
        versions: dict[str, str] = {}
        fetched, reused = 2, 0
        for hid in moenv_law.history_ids(listing):
            if str(hid) in previous:
                versions[str(hid)] = previous[str(hid)]
                reused += 1
                continue
            page = web.text(moenv_law.HISTORY.format(hid=hid, site_id=target.key))
            versions[str(hid)] = moenv_law.fragment(page)
            fetched += 1
        return {
            "site_id": target.key,
            "title": target.title,
            "kind": target.kind,
            "current": moenv_law.fragment(current),
            "history_ids": list(versions),
            "versions": versions,
            "_fetched": fetched,
            "_reused": reused,
        }

    def resolve(self, conn: Conn, bundle: dict[str, Any]) -> str:
        """以（現行版本標題，種類）對到語料的法規代碼。

        種類不在 `KINDS` 拋 `Skip`；同名的數部以現行版本日分辨，仍不是唯一一部拋
        `Unmatched`（`unmatched` 或 `ambiguous_title`）。
        """
        if bundle["kind"] not in self.KINDS:
            raise Skip
        current = moenv_law.parse_version(bundle["current"])
        # 同名的數部（廢止後另立同名法規）以現行版本日分辨。
        named = self._named(conn).get((current.title, bundle["kind"]), [])
        pcodes = [c for c, d in named if d == current.version_date] if len(named) > 1 else []
        pcodes = pcodes or [c for c, _ in named]
        if len(pcodes) != 1:
            code = "unmatched" if not pcodes else "ambiguous_title"
            raise Unmatched(code, f"{current.title}（{bundle['kind']}）")
        return pcodes[0]

    def versions(self, bundle: dict[str, Any]) -> tuple[Version, list[Version]]:
        """解析現行頁與每個歷史頁。"""
        current = moenv_law.parse_version(bundle["current"])
        return current, [moenv_law.parse_version(p) for p in bundle["versions"].values()]

    def _named(self, conn: Conn) -> dict[tuple[str, str], list[tuple[str, date]]]:
        """（最新版本的標題，種類）→（pcode，最新版本日）；歷來改過名的法規只以現名對。"""
        if self._titles is None:
            self._titles = {}
            for title, kind, pcode, latest in conn.execute(
                "SELECT DISTINCT ON (r.id) e.title, r.norm_kind::text, r.local_id, e.version_date"
                "  FROM corpus.legal_resource AS r"
                "  JOIN corpus.legal_expression AS e ON e.resource_id = r.id AND e.language = 'zho'"
                " WHERE r.id_scheme = 'moj_pcode'"
                " ORDER BY r.id, e.version_date DESC"
            ):
                for label, kinds in _NORM_KINDS.items():
                    if kind in kinds:
                        self._titles.setdefault((str(title), label), []).append(
                            (str(pcode), cast(date, latest))
                        )
        return self._titles


class Ly:
    """法律的歷史來源：立法院法律系統（制定機關）。"""

    feed = "ly_law"
    authority = "legislature"
    attribution = ly_law.ATTRIBUTION
    pause = 1.0
    # 該站只接受 TLS 1.2 與舊式重新協商（沒有 RFC 5746）
    legacy_tls = True
    # 立法院以「民法第一編總則」等分編命名、或名稱與全國法規資料庫不同者對不到
    unmatched_limit = 0.10

    def targets(self, conn: Conn, web: Web) -> list[Target]:
        """語料裡現行的法律（憲法不是立法院制定的，不在內），以最新版本的標題檢索。"""
        return [
            Target(str(pcode), str(title))
            for pcode, title in conn.execute(
                "SELECT DISTINCT ON (r.id) r.local_id, e.title"
                "  FROM corpus.legal_resource AS r"
                "  JOIN corpus.legal_expression AS e ON e.resource_id = r.id AND e.language = 'zho'"
                " WHERE r.id_scheme = 'moj_pcode' AND r.norm_kind = 'statute'"
                "   AND r.repealed_on IS NULL"
                " ORDER BY r.id, e.version_date DESC"
            )
        ]

    def bundle(self, web: Web, target: Target, previous: dict[str, Any]) -> dict[str, Any]:
        """一部法律的現行一版與過去各版的頁面內容；`previous` 已有的版本沿用不重抓。

        檢索結果沒有恰好一部同名的法律時，只記檢索到的標題（`hits`），不抓頁面。法律頁的
        最新一版與檢索結果不符拋 `UnreadablePage`。
        """
        name = ly_law.name(target.title)
        action, fields = ly_law.search_form(web.text(ly_law.HOME), name)
        hits = ly_law.hits(web.form(ly_law.BASE + action, fields))
        same = [h for h in hits if h.title == name]
        out: dict[str, Any] = {"pcode": target.key, "title": target.title}
        fetched, reused = 2, 0
        if len(same) != 1:
            out["hits"] = [h.title for h in hits]
            return out | {"_fetched": fetched, "_reused": reused}
        page = web.text(ly_law.BASE + same[0].link)
        fetched += 1
        entries = ly_law.entries(page)
        *past, latest = entries
        if latest.passed != same[0].passed:
            raise UnreadablePage(
                f"法律頁的最新一版 {latest.passed} 不是檢索結果的 {same[0].passed}"
            )
        versions: dict[str, Any] = {}
        for e in past:
            if e.key in previous:
                versions[e.key] = previous[e.key]
                reused += 1
                continue
            versions[e.key] = {
                "promulgated": e.promulgated.isoformat(),
                "page": ly_law.fragment(web.text(ly_law.BASE + e.link)),
            }
            fetched += 1
        out["current"] = {
            "promulgated": latest.promulgated.isoformat(),
            "page": ly_law.fragment(page),
        }
        out["versions"] = versions
        return out | {"_fetched": fetched, "_reused": reused}

    def resolve(self, conn: Conn, bundle: dict[str, Any]) -> str:
        """目標本身的 pcode；檢索沒有對到同名的現行法拋 `Unmatched`。"""
        if "current" not in bundle:
            hits = "、".join(bundle["hits"]) or "無"
            raise Unmatched("unmatched", f"{bundle['title']}：檢索結果的現行法為 {hits}")
        return str(bundle["pcode"])

    def versions(self, bundle: dict[str, Any]) -> tuple[Version, list[Version]]:
        """解析現行一版與過去各版；同日公布的數版只取後通過的一版。"""

        def parse(v: dict[str, str]) -> Version:
            return ly_law.parse_version(v["page"], date.fromisoformat(v["promulgated"]))

        # 同日公布的數版（分別三讀、同日公布）是累積的：後通過的一版已含先通過者的修正，取後者。
        by_day = {v.version_date: v for v in map(parse, bundle["versions"].values())}
        return parse(bundle["current"]), list(by_day.values())


SOURCES: dict[str, Callable[[], HistorySource]] = {"ly": Ly, "moenv": Moenv}
