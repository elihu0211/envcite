"""行政規則（函釋、裁量基準、作業性規定）的載入。

行政規則拘束機關內部，不直接對外發生法規範效力（行政程序法第 159 條；釋字第 137、216 號），
所以它不進語料的位階、不是推論的前提：推論結論旁附上主管機關對所依條文的見解（`guidance` schema）。

**錨點是文字。** 每個「解釋」附所解釋條文單元的原文，雜湊必須是該單元現行某版本列的雜湊，
與標註同一套：條文修正而文字改了，解釋不再自動附上。

**公文不可改。** 同一件（發文機關，字號）再載入時內容必須相同，相同則沿用（重複載入無害），
不同則整份拒絕。後函變更或廢止前函寫在後函的 `supersedes`：前函自後函發文日起停止適用
（釋字第 287 號），後函載明較晚的生效日時寫在該項的 `from`；沒有後函的停止寫 `ceased_on`。

由人或規則載入，`derivation.method` 不得為 `llm`。一次載入一個交易。

文件格式：

    {"derivation": {"method": "human", "agent": "…", "agent_version": "…"},
     "documents": [
       {"issuer": "環境部", "document_no": "環署水字第1234567890號", "issued_on": "2020-01-15",
        "kind": "interpretation", "subject": "<主旨>", "body": "<說明>",
        "published_in": "<公報出處，選填>", "source_url": "<選填>",
        "interprets": [{"law": "<pcode>", "path": "art_7/para_1", "text": "<原文>"}],
        "supersedes": [{"issuer": "環境部", "document_no": "<前函字號>", "from": "<選填>"}],
        "ceased_on": "<選填：逕行停止適用之日>"}]}

`kind`：`interpretation`（解釋性規定）、`discretion_standard`（裁量基準）、`operational`（作業性規定）。
"""

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import psycopg

from envcite_ontology.claims import AnnotationError, anchor
from envcite_ontology.db import Conn

log = logging.getLogger(__name__)


class GuidanceError(AnnotationError):
    """文件不合：錨定的文字不在語料、同一件公文內容不同、引用不存在的前函。"""


class DocumentChanged(GuidanceError):
    """同一件公文（發文機關，字號）已載入，內容與這次不同：公文不可改，由人判斷。"""


@dataclass(frozen=True)
class LoadedGuidance:
    """`load` 寫入一份行政規則文件的計數。"""

    #: 文件裡的公文件數，含沿用的。
    documents: int
    #: 已載入過、內容相同而沿用的件數
    repeated: int
    #: 新寫入的「解釋」（公文與所解釋條文單元的錨定）筆數。
    interpretations: int
    #: 新寫入的停止適用筆數。
    cessations: int


def load(conn: Conn, doc: Mapping[str, Any]) -> LoadedGuidance:
    """寫入一份行政規則文件；整份成功或整份不寫入。"""
    try:
        loaded = _load(conn, doc)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    log.info("行政規則載入：%s", loaded)
    return loaded


def _load(conn: Conn, doc: Mapping[str, Any]) -> LoadedGuidance:
    try:
        return _write(conn, doc)
    except GuidanceError:
        raise
    except AnnotationError as e:
        raise GuidanceError(str(e)) from e
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise GuidanceError(f"文件欄位缺漏或型別不合：{e!r}") from e
    except psycopg.errors.IntegrityError as e:
        raise GuidanceError(f"違反資料庫約束：{e}") from e
    except psycopg.errors.DataError as e:
        raise GuidanceError(f"值不合欄位型別：{e}") from e


def _write(conn: Conn, doc: Mapping[str, Any]) -> LoadedGuidance:
    derivation = doc["derivation"]
    d_id: list[int] = []

    def derivation_id() -> int:
        # 只在有新公文時才記推導活動：同步重跑沿用已載入的公文，不留下空的推導紀錄
        if not d_id:
            d_id.append(
                _one(
                    conn,
                    "INSERT INTO annotation.derivation (method, producer, producer_version)"
                    " VALUES (%s, %s, %s) RETURNING id",
                    derivation["method"],
                    derivation["agent"],
                    derivation["agent_version"],
                )
            )
        return d_id[0]

    # 先寫全部公文，再寫關係：後函可以引用同一份文件裡的前函
    ids: dict[tuple[str, str], int] = {}
    repeated = 0
    for d in doc["documents"]:
        key = (str(d["issuer"]), str(d["document_no"]))
        ids[key], again = _document(conn, d, derivation_id)
        repeated += again
    interpretations = cessations = 0
    for d in doc["documents"]:
        did = ids[(str(d["issuer"]), str(d["document_no"]))]
        for i in d.get("interprets", []):
            provision_id, digest = anchor(conn, "函釋", {**i, "id": d["document_no"]})
            interpretations += conn.execute(
                "INSERT INTO guidance.interpretation (document_id, provision_id, content_sha256)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (did, provision_id, digest),
            ).rowcount
        for earlier in d.get("supersedes", []):
            on = date.fromisoformat(str(earlier.get("from") or d["issued_on"]))
            cessations += _cease(conn, _existing(conn, ids, earlier), on, did)
        if d.get("ceased_on") is not None:
            cessations += _cease(conn, did, date.fromisoformat(str(d["ceased_on"])))
    return LoadedGuidance(len(ids), repeated, interpretations, cessations)


def _document(
    conn: Conn, d: Mapping[str, Any], derivation_id: Callable[[], int]
) -> tuple[int, int]:
    """寫入一件公文；已有同一件時內容必須相同。回傳（id，是否沿用）。"""
    digest = hashlib.sha256(f"{d['subject']}\n{d['body']}".encode()).digest()
    row = conn.execute(
        "SELECT id, content_sha256, issued_on, rule_kind::text FROM guidance.document"
        " WHERE issuer = %s AND document_no = %s",
        (d["issuer"], d["document_no"]),
    ).fetchone()
    if row is not None:
        same = (bytes(cast(bytes, row[1])), row[2], row[3]) == (
            digest,
            date.fromisoformat(str(d["issued_on"])),
            d["kind"],
        )
        if not same:
            raise DocumentChanged(f"{d['issuer']} {d['document_no']} 已載入，內容與這次不同")
        return cast(int, row[0]), 1
    return (
        _one(
            conn,
            "INSERT INTO guidance.document (issuer, document_no, issued_on, rule_kind, subject,"
            " body, published_in, source_url, derivation_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            d["issuer"],
            d["document_no"],
            date.fromisoformat(str(d["issued_on"])),
            d["kind"],
            d["subject"],
            d["body"],
            d.get("published_in"),
            d.get("source_url"),
            derivation_id(),
        ),
        0,
    )


def _existing(conn: Conn, ids: Mapping[tuple[str, str], int], ref: Mapping[str, Any]) -> int:
    key = (str(ref["issuer"]), str(ref["document_no"]))
    if key in ids:
        return ids[key]
    row = conn.execute(
        "SELECT id FROM guidance.document WHERE issuer = %s AND document_no = %s", key
    ).fetchone()
    if row is None:
        raise GuidanceError(f"引用了不存在的前函：{key[0]} {key[1]}")
    return cast(int, row[0])


def _cease(conn: Conn, document_id: int, on: date, by: int | None = None) -> int:
    """記一筆停止適用（有後函為 successor，否則為來源載明的 announced）；已記過時必須相同。"""
    cause = "announced" if by is None else "successor"
    row = conn.execute(
        "SELECT ceased_on, by_document_id, cause::text FROM guidance.cessation"
        " WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    if row is not None:
        if tuple(row) != (on, by, cause):
            raise GuidanceError(f"行政規則 {document_id} 已記有不同的停止適用")
        return 0
    conn.execute(
        "INSERT INTO guidance.cessation"
        " (document_id, issued_on, ceased_on, cause, by_document_id, by_issued_on)"
        " SELECT d.id, d.issued_on, %(on)s, %(cause)s, b.id, b.issued_on"
        "   FROM guidance.document AS d LEFT JOIN guidance.document AS b ON b.id = %(by)s"
        "  WHERE d.id = %(id)s",
        {"id": document_id, "on": on, "cause": cause, "by": by},
    )
    return 1


def _one(conn: Conn, sql: str, *args: object) -> int:
    row = conn.execute(sql, args).fetchone()
    assert row is not None
    return cast(int, row[0])
