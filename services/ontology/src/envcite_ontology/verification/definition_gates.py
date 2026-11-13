"""用語定義的驗證與接受：兩個互相獨立的方法，缺一不可。

- `literal`（確定性）：引文、用語與每個成員的名稱逐字在款的現行原文，且至少一個成員。
- `roundtrip`（另一家族的模型）：重讀者只看原文，說出被定義的用語與明文列舉的成員，與標註逐項相同。

與規範陳述同一套接受政策（`annotation.settle('definition', …)`）：必要方法最近一次都通過、沒有任何
方法最近一次失敗才確認；有失敗就駁回。成員漏列與多列都算不同——漏列的成員會被推論當成「不屬於」。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from psycopg.types.json import Jsonb

from envcite_ontology.db import Conn
from envcite_ontology.verification.context import ancestors_text, context_block
from envcite_ontology.verification.gates.base import GateResult
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.model_gates import eligible
from envcite_ontology.verification.models import Model, ModelError

log = logging.getLogger(__name__)

REQUIRED = ("literal", "roundtrip")

REREAD = """你是法規閱讀者。下面是「本法用語，定義如下」之下的一款原文。
請只依原文，輸出一個 JSON 物件：{{"term": "這一款被定義的用語，逐字取自原文",
"members": ["原文明文列舉、屬於這個用語的成員，逐字取自原文"]}}
原文沒有列舉成員就輸出空陣列，不要自己補。只輸出 JSON。

{context}原文：
{text}
"""


@dataclass(frozen=True)
class Definition:
    """一筆用語定義的標註，與核對它所需的原文。"""

    id: int
    path: str
    #: 款的現行原文
    text: str
    #: 標註所引的一段，應在 `text` 內
    quote: str
    term: str
    members: tuple[str, ...]
    #: 抽取者的名稱；驗證者不得是它
    extractor: str
    #: 上層條文，只供重讀者參考
    context: str


def definition_of(conn: Conn, definition_id: int) -> Definition:
    """讀出用語定義與它所在款的現行原文，成員依名稱排序。

    款已改版、標註對不上現行文字時沒有可核對的原文，丟 `LookupError`。
    """
    row = conn.execute(
        "SELECT p.id, p.path, v.content, t.definition_text, tk.name, d.producer"
        "  FROM annotation.term_definition AS t"
        "  JOIN corpus.provision AS p ON p.id = t.provision_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = t.provision_id"
        "   AND v.content_sha256 = t.content_sha256 AND v.superseded_at IS NULL"
        "  JOIN vocab.concept AS tk ON tk.id = t.term_concept_id"
        "  JOIN annotation.assertion AS tc ON tc.id = t.id"
        "  JOIN annotation.derivation AS d ON d.id = tc.derivation_id"
        " WHERE t.id = %s LIMIT 1",
        (definition_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"definition {definition_id} has no current text to check against")
    members = conn.execute(
        "SELECT mk.name FROM annotation.term_definition_member AS m"
        "  JOIN vocab.concept AS mk ON mk.id = m.member_concept_id"
        " WHERE m.definition_id = %s ORDER BY mk.name",
        (definition_id,),
    ).fetchall()
    pid, path, text, quote, term, producer = row
    return Definition(
        definition_id,
        str(path),
        str(text),
        str(quote),
        str(term),
        tuple(str(m[0]) for m in members),
        str(producer),
        ancestors_text(conn, cast(int, pid)),
    )


@dataclass(frozen=True)
class LiteralGate:
    """引文、用語與每個成員的名稱逐字在原文；至少一個成員。只查標註與原文的字面，不查法律對錯。"""

    method: str = "literal"
    verifier: str = "gate:literal@1"

    def check(self, conn: Conn, definition_id: int) -> GateResult:
        """引文、用語、成員都在原文且至少一個成員為 pass，否則為 fail；不會 inconclusive。"""
        d = definition_of(conn, definition_id)
        missing = [x for x in (d.term, *d.members) if x not in d.text]
        problems: list[str] = []
        if d.quote not in d.text:
            problems.append("quote is not in the text")
        if missing:
            problems.append("not in the text: " + "、".join(missing))
        if not d.members:
            problems.append("no members")
        if problems:
            return GateResult("fail", {"problems": problems})
        return GateResult("pass", {"term": d.term, "members": list(d.members)})


@dataclass(frozen=True)
class DefinitionRoundtripGate:
    """另一家族的模型只看原文，重讀被定義的用語與成員；與標註逐項相同才通過。"""

    reader: Model
    allow_same_family: bool = False
    method: str = "roundtrip"

    @property
    def verifier(self) -> str:
        """記入紀錄的驗證者識別，含重讀者的名稱。"""
        return f"gate:definition-roundtrip@1:{self.reader.name}"

    def check(self, conn: Conn, definition_id: int) -> GateResult:
        """重讀的用語與成員（忽略空白、不論順序）同標註為 pass，不同為 fail。

        沒有合格的重讀者（與抽取者同名，預設也不得同家族）、答案取不出 JSON 或成員不是字串清單，
        都是 inconclusive。
        """
        d = definition_of(conn, definition_id)
        ok, skipped = eligible([self.reader], d.extractor, self.allow_same_family)
        if not ok:
            return GateResult(
                "inconclusive", {"reason": "no eligible verifier", "skipped": skipped}
            )
        answer = object_from(
            ok[0].complete(REREAD.format(text=d.text, context=context_block(d.context)))
        )
        if answer is None:
            return GateResult("inconclusive", {"reason": "unparseable answer"})
        raw_members = answer.get("members")
        if not isinstance(raw_members, list) or not all(isinstance(m, str) for m in raw_members):
            return GateResult("inconclusive", {"reason": "members is not a list of strings"})
        theirs = (_norm(str(answer.get("term", ""))), sorted(_norm(m) for m in raw_members))
        mine = (_norm(d.term), sorted(_norm(m) for m in d.members))
        if mine == theirs:
            return GateResult("pass", {"verifier": ok[0].name})
        return GateResult(
            "fail",
            {
                "verifier": ok[0].name,
                "annotation": {"term": mine[0], "members": mine[1]},
                "reread": {"term": theirs[0], "members": theirs[1]},
            },
        )


def _norm(text: str) -> str:
    return "".join(text.split())


type DefinitionGate = LiteralGate | DefinitionRoundtripGate


def gates_for(reader: Model | None, allow_same_family: bool = False) -> tuple[DefinitionGate, ...]:
    """確定性閘門加（有重讀者時）往返閘門。"""
    if reader is None:
        return (LiteralGate(),)
    return (LiteralGate(), DefinitionRoundtripGate(reader, allow_same_family))


def verify(conn: Conn, gates: Sequence[DefinitionGate]) -> int:
    """對每個 extracted 的定義跑尚無定論的閘門並記錄；回傳新寫入的驗證列數。"""
    written = 0
    ids = [
        cast(int, row[0])
        for row in conn.execute(
            "SELECT id FROM annotation.assertion"
            " WHERE assertion_kind = 'definition' AND confirm_state = 'extracted' ORDER BY id"
        )
    ]
    for did in ids:
        for gate in gates:
            latest = conn.execute(
                "SELECT result::text FROM annotation.verification"
                " WHERE assertion_id = %s AND method = %s ORDER BY id DESC LIMIT 1",
                (did, gate.method),
            ).fetchone()
            if latest is not None and latest[0] != "inconclusive":
                continue
            try:
                result = gate.check(conn, did)
            except ModelError as e:
                result = GateResult("inconclusive", {"error": str(e)[:300]})
            conn.execute(
                "SELECT annotation.record_verification("
                "%s, %s, %s, %s::annotation.gate_result, %s::jsonb)",
                (did, gate.method, gate.verifier, result.verdict, Jsonb(result.evidence)),
            )
            written += 1
        conn.commit()
    return written


@dataclass(frozen=True)
class SettledDefinitions:
    """定義的接受結果：升為 confirmed 與 rejected 的數目。"""

    confirmed: int
    rejected: int


def settle(
    conn: Conn, gates: Sequence[DefinitionGate], required: Sequence[str] = REQUIRED
) -> SettledDefinitions:
    """驗證、接受。定義之間沒有相依，一輪就夠。"""
    verify(conn, gates)
    row = conn.execute(
        "SELECT confirmed, rejected FROM annotation.settle('definition', %s)", (list(required),)
    ).fetchone()
    conn.commit()
    assert row is not None
    log.info("定義：%d 個確認、%d 個駁回", row[0], row[1])
    return SettledDefinitions(cast(int, row[0]), cast(int, row[1]))
