"""地方自治法規（22 縣市標準法規與自治法規資料庫）。

涵蓋臺北市、新北市、桃園市、臺中市、臺南市、高雄市等 22 直轄市與縣市之
自治條例、自治規則、委辦規則。
"""

from __future__ import annotations

import hashlib
import json
import ssl
import zipfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from envcite_ontology.acquire.engine import Contents, Feed, InvalidSnapshot, NotAnArchive

_BASE = "https://glrs.moi.gov.tw/api/local"


def canonical_sha256(item: object) -> bytes:
    canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).digest()


class LocalGlrs:
    name = "local-glrs"
    attribution = "地方自治法規開放資料（內政部與 22 縣市政府）；政府資料開放授權條款"
    feeds: Sequence[Feed] = (Feed("local_glrs", f"{_BASE}/json"),)

    def tls(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return context

    def read(self, path: Path) -> Contents:
        data_date, laws = load_local_laws(path)
        return Contents(
            data_date=data_date,
            items=[(str(law.get("Id", "")), canonical_sha256(law)) for law in laws],
        )


def load_local_laws(path: Path) -> tuple[date, list[dict[str, Any]]]:
    """讀取地方自治法規快照檔。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".json")]
            if not names:
                raise InvalidSnapshot(f"expected at least one JSON data file, found {names}")
            data = json.loads(z.read(names[0]).decode("utf-8-sig"))
            laws = data.get("Laws", [])
            return date.today(), laws
    except zipfile.BadZipFile as e:
        raise NotAnArchive(str(e)) from e
    except FileNotFoundError as e:
        raise InvalidSnapshot(f"snapshot file missing: {e.filename}") from e
