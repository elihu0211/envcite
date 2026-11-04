"""處罰條文的依賴圖：處罰條文以「違反第 N 條」罰的條文，由確定性的條號解析寫成
`annotation.violation`。

兩種：`duty` 是所罰義務所在的條（項、款）；`delegated_rule` 是「違反依第 N 條所定辦法」的
授權條文，義務在授權命令裡，沿授權（`annotation.delegation`）找命令。`envcite-sync` 在匯入後
對全庫寫入（冪等）；
**改解析規則要遞增 `RULE_VERSION`**，`annotation.current_violation` 只看最新版本。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence

import psycopg

from envcite_ontology.ingest.numerals import CN_NUMERALS, parse_int

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

RULE_VERSION = 1

NUMBER = f"0-9{CN_NUMERALS}"


def cn_number(s: str) -> int:
    """條、項、款號：「二十七」→ 27、「27」→ 27；讀不出拋 ValueError。"""
    return int(s) if s.isdigit() else parse_int(s)


#: 條、項、款各一個記號；「之」只接在條後。
_TOKEN = re.compile(rf"第([{NUMBER}]+)(條|項|款)(?:之([{NUMBER}]+))?")
#: 兩個記號之間只有這些字時，後者接續前者的條（項）：「第二十七條第一項、第四項」。
_JOINER = re.compile(r"[、或及與和暨]*")
_LEVEL = {"條": 0, "項": 1, "款": 2}
_SEGMENT = ("art", "para", "pt")


def _path(parts: list[str]) -> str:
    return "/".join(f"{_SEGMENT[i]}_{n}" for i, n in enumerate(parts))


def citations(text: str) -> list[str]:
    """原文引用的條（項、款）路徑，依出現順序、不重複，每串只記最深的一層。

    「第七條第一項」→ art_7/para_1；「第五條之一」→ art_5-1；「第二十七條第一項、第四項」→
    art_27/para_1、art_27/para_4（接續的項、款沿用前面的條）；「第一項至第三項」展開成三項。
    沒有條作開頭的項、款（「第二項」指本條）不在此列。
    """
    out: list[str] = []
    parts: list[str] = []  # 目前這串引用的條、項、款
    end = -1
    for m in _TOKEN.finditer(text):
        try:
            number = cn_number(m.group(1))
            suffix = f"-{cn_number(m.group(3))}" if m.group(3) else ""
        except ValueError:
            parts = []
            continue
        level = _LEVEL[m.group(2)]
        gap = text[end : m.start()] if parts else None
        if level == 0:
            parts = [f"{number}{suffix}"]
        elif gap is not None and len(parts) >= level and (gap == "至" or _JOINER.fullmatch(gap)):
            if gap == "至" and len(parts) == level + 1:
                out += [
                    _path([*parts[:level], str(n)]) for n in range(int(parts[level]) + 1, number)
                ]
            if gap == "" and out and out[-1] == _path(parts):
                out.pop()  # 更深的一層取代它：「第七條」之後緊接「第一項」
            parts = [*parts[:level], str(number)]
        else:
            parts = []
            continue
        end = m.end()
        if _path(parts) not in out:
            out.append(_path(parts))
    return out


#: 罰則所罰的行為：「違反第七條…者」「規避、妨礙或拒絕依第二十六條…之查證」。
_VIOLATION = re.compile(r"(?:違反|拒絕)([^，；。]*?)(?:者|，|；|。|$)")
#: 「違反（依）第九條第二項所定之…」罰的是授權命令的義務，被引的是授權條文，不在此列。
_DELEGATED = re.compile(rf"依?(?:第[{NUMBER}]+[條項款](?:之[{NUMBER}]+)?[、或及與和]?)+所定")
_PUNISHES = re.compile(r"處(?:新臺幣|[^，；。]{0,8}?(?:徒刑|拘役|罰金))")


def violations(text: str) -> list[str]:
    """處罰條文所罰的義務所在的條（項、款）：「違反」「拒絕」之後引用的；沒有這類句子時是全部引用
    （「依第二十二條有申報義務，不為申報者」）。授權命令的授權條文不算；不是處罰條文就沒有。
    """
    if not _PUNISHES.search(text):
        return []
    out: list[str] = []
    for m in _VIOLATION.finditer(text):
        out += [c for c in citations(_DELEGATED.sub("", m.group(1))) if c not in out]
    return out or citations(_DELEGATED.sub("", text))


def delegated_rules(text: str) -> list[str]:
    """處罰條文所罰的授權命令的授權條文：「違反（依）第十三條第四項所定辦法者」→ art_13/para_4。"""
    if not _PUNISHES.search(text):
        return []
    out: list[str] = []
    for m in _DELEGATED.finditer(text):
        out += [c for c in citations(m.group(0)) if c not in out]
    return out


def within(path: str, cited: Sequence[str]) -> bool:
    """單元落在某個引用裡：就是它、在它之下，或是它的上層（引款而陳述錨在項的列舉句）。"""
    return any(path == c or path.startswith(c + "/") or c.startswith(path + "/") for c in cited)


def extract(conn: Conn) -> int:
    """寫入語料所有現行條文的處罰依賴圖；回傳新寫入的列數。不 commit，由呼叫端決定。"""
    started = time.monotonic()
    rows = conn.execute(
        "SELECT v.provision_id, p.resource_id, v.content_sha256, v.content"
        "  FROM corpus.provision_version AS v"
        "  JOIN corpus.provision AS p ON p.id = v.provision_id"
        " WHERE v.superseded_at IS NULL AND v.content IS NOT NULL AND v.content ~ '處'"
        "   AND NOT EXISTS (SELECT 1 FROM annotation.violation AS vi"
        "                    WHERE vi.provision_id = v.provision_id"
        "                      AND vi.content_sha256 = v.content_sha256"
        "                      AND vi.rule_version = %s)",
        (RULE_VERSION,),
    ).fetchall()
    found = [
        (pid, digest, cited, rid, cited, kind, RULE_VERSION)
        for pid, rid, digest, text in rows
        for kind, paths in (
            ("duty", violations(str(text))),
            ("delegated_rule", delegated_rules(str(text))),
        )
        for cited in paths
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO annotation.violation"
            " (provision_id, content_sha256, cited_path, cited_provision_id, violation_kind,"
            "  rule_version)"
            " SELECT %s, %s, %s,"
            "  (SELECT id FROM corpus.provision WHERE resource_id = %s AND path = %s), %s, %s"
            " ON CONFLICT DO NOTHING",
            found,
        )
    log.info(
        "處罰依賴圖：新寫入 %d 列（%d 個條文單元待解析），%.1f 秒",
        len(found),
        len(rows),
        time.monotonic() - started,
    )
    return len(found)
