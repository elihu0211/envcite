"""行政法院裁判的載入與撤下。

裁判與函釋同屬解釋依據（見 `canon.law.guidance`）：不是構成要件基準，不改條文的效力區間，
不是推論的前提；錨定到（條文單元，內容雜湊），條文改字後不再自動附到新文字。

**錨點可以是舊文字。** 裁判適用行為時或裁處時的條文，不是現行文字，所以附的原文雜湊只要是
該單元某一版本列的雜湊即可，不必現行。`basis` 記錄版本怎麼定的：`stated` 是裁判理由載明並對應到
唯一版本，`decided_on` 是理由未載明、以裁判日代替，後者的原文必須是裁判日現行的版本。
從理由抽出引用與判定版本在載入之前，這裡只驗證抽出的結果。

**同一 jid 是同一筆。** 再載入時內容相同則沿用；內容或錨定不同則整筆取代（含錨定邊）。

**撤下是刪除。** 來源對某 jid 回應「查無資料」時，`take_down` 刪除該裁判的內容、錨定邊與
一切衍生物（資料庫外鍵 ON DELETE CASCADE），只留 jid 與偵測日的墓碑。來源之後再出現同一 jid，
載入會移除墓碑。

由人或規則載入，`derivation.method` 不得為 `llm`。一次載入一個交易。

文件格式（由 `_Document` 驗證，缺欄位或型別不合整份拒絕）：

    {"derivation": {"method": "rule", "agent": "…", "agent_version": "…"},
     "decisions": [
       {"jid": "TPAA,110,大,1,20210101,1", "decided_on": "2021-01-01",
        "instance": "supreme", "kind": "ordinary", "title": "<案由>", "body": "<全文>",
        "source_url": "<選填>",
        "cites": [{"law": "<pcode>", "path": "art_7/para_1", "text": "<原文>",
                   "basis": "stated"}]}]}

`instance`：`supreme`、`high`、`district`。`kind`：`ordinary`（預設）、`grand_chamber`
（只能在 `supreme`）。
"""

import hashlib
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, NamedTuple, Self

import psycopg
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from canon.claims.anchoring import provision
from canon.claims.errors import AnnotationError
from canon.db import Conn, fetch, fetch_one, scalar

log = logging.getLogger(__name__)

#: jid 第一欄是法院別加類別碼，類別碼在末位；A 為行政類。
_ADMINISTRATIVE_JID = re.compile(r"^[A-Z]*A,")


class _Derivation(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: str
    agent: str
    agent_version: str


class _Cite(BaseModel):
    """裁判適用的條文單元，附它所讀的原文與版本怎麼定的。"""

    model_config = ConfigDict(frozen=True)

    law: str
    path: str
    text: str
    basis: Literal["stated", "decided_on"]


class _Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    jid: str
    decided_on: date
    instance: Literal["supreme", "high", "district"]
    kind: Literal["ordinary", "grand_chamber"] = "ordinary"
    title: str
    body: str
    source_url: str | None = None
    cites: list[_Cite] = []

    @field_validator("jid")
    @classmethod
    def _administrative(cls, jid: str) -> str:
        if not _ADMINISTRATIVE_JID.match(jid):
            raise ValueError(f"jid {jid!r} 不是行政類裁判（第一欄末位須為 A）")
        return jid

    @model_validator(mode="after")
    def _grand_chamber_is_supreme(self) -> Self:
        if self.kind == "grand_chamber" and self.instance != "supreme":
            raise ValueError("大法庭設於最高行政法院")
        return self


class _Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    derivation: _Derivation
    decisions: list[_Decision]


class JudgmentError(AnnotationError):
    """文件不合：非行政類、錨定的文字不在語料或不是該版本的文字。"""


@dataclass(frozen=True)
class LoadedJudgments:
    """`load` 寫入一份裁判文件的計數。"""

    #: 文件裡的裁判筆數，含沿用的。
    decisions: int
    #: 已載入過、內容與錨定相同而沿用的筆數。
    repeated: int
    #: 已載入過、內容或錨定不同而取代的筆數。
    replaced: int
    #: 新寫入的錨定邊筆數（取代時含重寫的）。
    anchors: int


@dataclass(frozen=True)
class TakenDown:
    """`take_down` 的結果。"""

    #: 刪除了內容的裁判筆數。
    deleted: int
    #: 新記的墓碑筆數；已有墓碑的 jid 保留第一次偵測日。
    tombstoned: int


def load(conn: Conn, doc: Mapping[str, object]) -> LoadedJudgments:
    """寫入一份裁判文件；整份成功或整份不寫入。"""
    try:
        loaded = _load(conn, doc)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    log.info("行政法院裁判載入：%s", loaded)
    return loaded


def take_down(conn: Conn, jids: Sequence[str], detected_on: date) -> TakenDown:
    """來源回應「查無資料」的裁判：刪除內容與錨定邊，只留墓碑；整批成功或整批不寫入。"""
    try:
        deleted = tombstoned = 0
        for jid in dict.fromkeys(jids):
            if not jid:
                raise JudgmentError("jid 不可為空")
            deleted += conn.execute("DELETE FROM judgment.decision WHERE jid = %s", (jid,)).rowcount
            tombstoned += conn.execute(
                "INSERT INTO judgment.takedown (jid, detected_on) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (jid, detected_on),
            ).rowcount
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    result = TakenDown(deleted, tombstoned)
    log.info("行政法院裁判撤下：%s", result)
    return result


def _load(conn: Conn, doc: Mapping[str, object]) -> LoadedJudgments:
    try:
        return _write(conn, _Document.model_validate(dict(doc)))
    except JudgmentError:
        raise
    except ValidationError as e:
        raise JudgmentError(f"文件欄位缺漏或型別不合：{e}") from e
    except AnnotationError as e:
        raise JudgmentError(str(e)) from e
    except psycopg.errors.IntegrityError as e:
        raise JudgmentError(f"違反資料庫約束：{e}") from e
    except psycopg.errors.DataError as e:
        raise JudgmentError(f"值不合欄位型別：{e}") from e


class _Anchor(NamedTuple):
    provision_id: int
    content_sha256: bytes
    basis: str


class _Row(NamedTuple):
    id: int
    content_sha256: bytes
    decided_on: date
    instance: str
    kind: str


def _write(conn: Conn, doc: _Document) -> LoadedJudgments:
    d_id: list[int] = []

    def derivation_id() -> int:
        # 只在有新內容時才記推導活動：重跑沿用已載入的裁判，不留下空的推導紀錄
        if not d_id:
            d_id.append(
                scalar(
                    conn,
                    "INSERT INTO annotation.derivation (method, producer, producer_version)"
                    " VALUES (%s, %s, %s) RETURNING id",
                    (
                        doc.derivation.method,
                        doc.derivation.agent,
                        doc.derivation.agent_version,
                    ),
                    int,
                )
            )
        return d_id[0]

    repeated = replaced = anchors = 0
    for d in doc.decisions:
        wanted = _anchors(conn, d)
        outcome = _decision(conn, d, wanted, derivation_id)
        repeated += outcome == "repeated"
        replaced += outcome == "replaced"
        anchors += 0 if outcome == "repeated" else len(wanted)
    return LoadedJudgments(len(doc.decisions), repeated, replaced, anchors)


def _anchors(conn: Conn, d: _Decision) -> frozenset[_Anchor]:
    """每個引用的（單元，內容雜湊，依據）；原文須是該單元某一版本，`decided_on` 須裁判日現行。

    已被重新匯入取代的列（`superseded_at` 非空）不算版本。
    """
    found: set[_Anchor] = set()
    for cite in d.cites:
        provision_id = provision(conn, cite.law, cite.path)
        digest = hashlib.sha256(cite.text.encode()).digest()
        if cite.basis == "decided_on":
            ok = conn.execute(
                "SELECT 1 FROM corpus.provision_version"
                " WHERE provision_id = %s AND content_sha256 = %s AND superseded_at IS NULL"
                " AND force_period @> %s::date",
                (provision_id, digest, d.decided_on),
            ).fetchone()
            if ok is None:
                raise JudgmentError(
                    f"裁判 {d.jid} 錨定的文字不是 {cite.law} {cite.path} 在裁判日 {d.decided_on}"
                    " 現行的文字"
                )
        else:
            ok = conn.execute(
                "SELECT 1 FROM corpus.provision_version"
                " WHERE provision_id = %s AND content_sha256 = %s AND superseded_at IS NULL",
                (provision_id, digest),
            ).fetchone()
            if ok is None:
                raise JudgmentError(
                    f"裁判 {d.jid} 錨定的文字不是 {cite.law} {cite.path} 任何版本的文字"
                )
        found.add(_Anchor(provision_id, digest, cite.basis))
    return frozenset(found)


def _decision(
    conn: Conn,
    d: _Decision,
    wanted: frozenset[_Anchor],
    derivation_id: Callable[[], int],
) -> Literal["added", "repeated", "replaced"]:
    """寫入一筆裁判；已有同一 jid 時內容與錨定都相同才沿用，否則整筆取代。"""
    digest = hashlib.sha256(f"{d.title}\n{d.body}".encode()).digest()
    row = fetch_one(
        conn,
        "SELECT id, content_sha256, decided_on, instance::text, kind::text"
        " FROM judgment.decision WHERE jid = %s",
        (d.jid,),
        _Row,
    )
    if row is None:
        # 來源再度提供的 jid 不再是撤下的
        conn.execute("DELETE FROM judgment.takedown WHERE jid = %s", (d.jid,))
        decision_id = scalar(
            conn,
            "INSERT INTO judgment.decision (jid, decided_on, instance, kind, title, body,"
            " source_url, derivation_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                d.jid,
                d.decided_on,
                d.instance,
                d.kind,
                d.title,
                d.body,
                d.source_url,
                derivation_id(),
            ),
            int,
        )
        _write_anchors(conn, decision_id, wanted)
        return "added"
    current = _stored_anchors(conn, row.id)
    same = (row.content_sha256, row.decided_on, row.instance, row.kind) == (
        digest,
        d.decided_on,
        d.instance,
        d.kind,
    )
    if same and current == wanted:
        return "repeated"
    conn.execute(
        "UPDATE judgment.decision SET decided_on = %s, instance = %s, kind = %s, title = %s,"
        " body = %s, source_url = %s, derivation_id = %s WHERE id = %s",
        (d.decided_on, d.instance, d.kind, d.title, d.body, d.source_url, derivation_id(), row.id),
    )
    conn.execute("DELETE FROM judgment.anchor WHERE decision_id = %s", (row.id,))
    _write_anchors(conn, row.id, wanted)
    return "replaced"


def _stored_anchors(conn: Conn, decision_id: int) -> frozenset[_Anchor]:
    return frozenset(
        fetch(
            conn,
            "SELECT provision_id, content_sha256, basis::text FROM judgment.anchor"
            " WHERE decision_id = %s",
            (decision_id,),
            _Anchor,
        )
    )


def _write_anchors(conn: Conn, decision_id: int, anchors: frozenset[_Anchor]) -> None:
    for a in sorted(anchors):
        conn.execute(
            "INSERT INTO judgment.anchor (decision_id, provision_id, content_sha256, basis)"
            " VALUES (%s, %s, %s, %s)",
            (decision_id, a.provision_id, a.content_sha256, a.basis),
        )
