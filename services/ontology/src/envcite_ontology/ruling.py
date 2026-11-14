"""違憲失效（能力問題 A5）：大法官解釋與憲法法庭判決所宣告失效的條文。

**由人載入，不從文字推斷。** 失效日在解釋文裡是自然語言，來源網站（憲法法庭）只有 HTML，
沒有結構化的欄位；所以每則裁判附解釋文原文與出處，失效日由載入的人讀原文填。
**裁判不可改**：同一字號再載入時內容必須相同，相同則沿用。一次載入一個交易。

文件格式（由 `_Document` 驗證，缺欄位或型別不合整份拒絕）：

    {"rulings": [
       {"ruling_no": "<釋字第N號 或 N年憲判字第M號>", "kind": "interpretation | judgment",
        "decided_on": "<公布或宣判日>", "source_url": "https://cons.judicial.gov.tw/…",
        "excerpt": "<解釋文／主文中宣告失效的原文>",
        "invalidates": [{"law": "<pcode>", "path": "art_7/para_1", "invalid_from": "<失效日>",
                         "partial": false}]}]}

條文單元必須在語料裡；一個條文單元對同一則裁判只記一次。`partial` 可省略（預設否）。

兩個入口：`envcite-ruling load <檔>` 載入上述文件（人讀原文填失效日）；`envcite-ruling fetch`
自憲法法庭網站取得裁判並由規則提出失效（`ruling_sync`），兩者可並存：抽取流程只刪改自己寫的列。
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, NamedTuple

import psycopg
from pydantic import BaseModel, ConfigDict, ValidationError

from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.corpus_ref import UnknownProvision, provision_id
from envcite_ontology.db import Conn, fetch_one, scalar

log = logging.getLogger(__name__)


class RulingError(AnnotationError):
    """文件不合：條文單元不在語料、同一字號內容不同、失效日早於裁判日。"""


class _Invalidation(BaseModel):
    """一則裁判宣告失效的一個條文單元。"""

    model_config = ConfigDict(frozen=True)

    #: 法規的 pcode。
    law: str
    path: str
    invalid_from: date
    partial: bool = False


class _Ruling(BaseModel):
    model_config = ConfigDict(frozen=True)

    ruling_no: str
    kind: Literal["interpretation", "judgment"]
    decided_on: date
    source_url: str
    excerpt: str
    invalidates: list[_Invalidation]


class _Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    rulings: list[_Ruling]


class _RulingRow(NamedTuple):
    id: int
    kind: str
    decided_on: date
    source_url: str
    excerpt: str


@dataclass(frozen=True)
class LoadedRulings:
    """`load` 寫入一份裁判文件的計數。"""

    #: 文件裡的裁判數，含沿用的。
    rulings: int
    #: 已載入過、內容相同而沿用的件數
    repeated: int
    #: 新寫入的失效條文單元數。
    invalidations: int


def load(conn: Conn, doc: Mapping[str, object]) -> LoadedRulings:
    """寫入一份裁判文件；整份成功或整份不寫入。"""
    try:
        loaded = _write(conn, doc)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    log.info("違憲失效載入：%s", loaded)
    return loaded


def _write(conn: Conn, doc: Mapping[str, object]) -> LoadedRulings:
    try:
        return _rulings(conn, _Document.model_validate(dict(doc)))
    except RulingError:
        raise
    except ValidationError as e:
        raise RulingError(f"文件欄位缺漏或型別不合：{e}") from e
    except (AnnotationError, UnknownProvision) as e:
        raise RulingError(str(e)) from e
    except psycopg.errors.IntegrityError as e:
        raise RulingError(f"違反資料庫約束：{e}") from e
    except psycopg.errors.DataError as e:
        raise RulingError(f"值不合欄位型別：{e}") from e
    except psycopg.errors.RaiseException as e:
        raise RulingError(str(e)) from e


def _rulings(conn: Conn, doc: _Document) -> LoadedRulings:
    count = repeated = invalidations = 0
    for r in doc.rulings:
        count += 1
        ruling_id, again = _ruling(conn, r)
        repeated += again
        for i in r.invalidates:
            unit = provision_id(conn, i.law, i.path)
            invalidations += conn.execute(
                "INSERT INTO corpus.ruling_invalidation"
                " (ruling_id, resource_id, provision_id, invalid_from, partial)"
                " SELECT %s, resource_id, id, %s, %s FROM corpus.provision WHERE id = %s"
                " ON CONFLICT (ruling_id, provision_id) DO NOTHING",
                (ruling_id, i.invalid_from, i.partial, unit),
            ).rowcount
    return LoadedRulings(count, repeated, invalidations)


def _ruling(conn: Conn, r: _Ruling) -> tuple[int, int]:
    """寫入一則裁判；已有同一字號時內容必須相同。回傳（id，是否沿用）。"""
    row = fetch_one(
        conn,
        "SELECT id, ruling_kind::text, decided_on, source_url, excerpt"
        " FROM corpus.constitutional_ruling WHERE ruling_no = %s",
        (r.ruling_no,),
        _RulingRow,
    )
    if row is not None:
        stored = (row.kind, row.decided_on, row.source_url, row.excerpt)
        if stored != (r.kind, r.decided_on, r.source_url, r.excerpt):
            raise RulingError(f"{r.ruling_no} 已載入，內容與這次不同")
        return row.id, 1
    new = scalar(
        conn,
        "INSERT INTO corpus.constitutional_ruling"
        " (ruling_no, ruling_kind, decided_on, source_url, excerpt)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (r.ruling_no, r.kind, r.decided_on, r.source_url, r.excerpt),
        int,
    )
    return new, 0
