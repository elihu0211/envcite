"""全國法規資料庫 Open API。

`/api/ch/law/json` 與 `/api/ch/order/json` 各回傳一個 ZIP，內含一個 JSON 資料檔：
`{"UpdateDate": "2026/9/18 上午 12:00:00", "Laws": [...]}`。只能整批下載；網站頁面的
robots.txt 為 `Disallow: /`，API 是唯一管道。
"""

import hashlib
import json
import re
import ssl
import zipfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from envcite_ontology.acquire.engine import Contents, Feed, InvalidSnapshot, NotAnArchive
from envcite_ontology.http import tls_context

_BASE = "https://law.moj.gov.tw/api/ch"
_PCODE = re.compile(r"[?&]pcode=([A-Z][0-9]{7})(?:&|$)")
_UPDATE_DATE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})\b")


class Moj:
    """全國法規資料庫的 `Source`：法律與命令各一個全量檔。"""

    name = "moj"
    attribution = "全國法規資料庫（法務部）；政府資料開放授權條款－第1版"
    feeds: Sequence[Feed] = (
        Feed("moj_law", f"{_BASE}/law/json"),
        Feed("moj_order", f"{_BASE}/order/json"),
    )

    def tls(self) -> ssl.SSLContext:
        """連全國法規資料庫的 TLS 設定。"""
        return tls_context()

    def read(self, path: Path) -> Contents:
        """回傳 `UpdateDate` 與每部法規的（pcode，內容雜湊）。"""
        data_date, laws = load_laws(path)
        return Contents(
            data_date=data_date, items=[(pcode(law), content_sha256(law)) for law in laws]
        )


def load_laws(path: Path) -> tuple[date, list[dict[str, Any]]]:
    """快照檔的資料日期與 `Laws` 陣列。格式不符拋 InvalidSnapshot。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".json")]
            if len(names) != 1:
                raise InvalidSnapshot(f"expected one JSON data file, found {names}")
            data = json.loads(z.read(names[0]).decode("utf-8-sig"))
    except zipfile.BadZipFile as e:
        raise NotAnArchive(str(e)) from e
    except FileNotFoundError as e:
        raise InvalidSnapshot(f"snapshot file missing: {e.filename}") from e
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InvalidSnapshot(str(e)) from e

    match = _UPDATE_DATE.match(str(data.get("UpdateDate", "")))
    if match is None:
        raise InvalidSnapshot(f"unreadable UpdateDate: {data.get('UpdateDate')!r}")
    laws = data.get("Laws")
    if not isinstance(laws, list):
        raise InvalidSnapshot("missing Laws")
    return date(*(int(g) for g in match.groups())), laws


def pcode(law: object) -> str:
    """一部法規的 pcode，取自 `LawURL` 的 `pcode` 參數；取不到拋 `InvalidSnapshot`。"""
    url = law.get("LawURL", "") if isinstance(law, dict) else ""
    match = _PCODE.search(str(url))
    if match is None:
        raise InvalidSnapshot(f"no pcode in LawURL: {url!r}")
    return match.group(1)


def content_sha256(law: object) -> bytes:
    """一部法規的內容雜湊：鍵排序、緊湊正規化後的 JSON 的 SHA-256。"""
    # 正規化後再雜湊：鍵的順序不同不算內容改變。
    canonical = json.dumps(law, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).digest()
