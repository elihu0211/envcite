"""標註文件的形狀：`claims.load` 收到的 JSON，在邊界驗證並解碼成型別化的結構。

欄位的名稱與意義見 `claims` 套件說明；這裡只管形狀（缺欄位、型別不合整份拒絕）。
列舉值（種類、義務方向、比較子、罰則效果…）以字串收下，由資料庫的同名列舉與觸發器強制，
所以不合的值仍由資料庫擋下，訊息不變。未知的額外鍵忽略，抽取器可帶額外的標記。
"""

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _as_id(value: object) -> str:
    """文件裡的陳述 id 可寫成字串或整數；其餘（含布林）不是 id。"""
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError("an id must be a string or an integer")
    return str(value)


def _empty_is_absent(value: object) -> object:
    """空的區間（`[]`）視同沒有區間。"""
    return value or None


type Id = Annotated[str, BeforeValidator(_as_id)]
type Span = Annotated[tuple[int, int] | None, BeforeValidator(_empty_is_absent)]
type Text = Annotated[str, BeforeValidator(str)]


class _Part(BaseModel):
    model_config = ConfigDict(frozen=True)


class Derivation(_Part):
    """這份文件的來源推導：誰、用什麼方法、哪個版本寫的。"""

    method: str
    agent: str
    agent_version: str


class Scheme(_Part):
    """詞彙方案；有 `law` 則只及於那部法規。"""

    code: str
    title: str
    law: str | None = None


class Concept(_Part):
    """方案下的一個概念：代碼、名稱、別名與外部對映。"""

    code: str
    label: str
    aliases: list[str] = []
    #: 外部對映（外部體系 → 識別碼），只記等同。
    matches: dict[str, Text] | None = None
    #: 詞彙沒有上下位：帶了就拒絕（見 `claims.vocabulary.write_concepts`），所以只需要知道有沒有寫。
    broader: Any = None


class Slot(_Part):
    """陳述的一個角色槽位的值：概念或字面，主體可附責任基礎，或標為未明示。"""

    concept: str | None = None
    literal: str | None = None
    liability: str | None = None
    unstated: bool | None = None


class Delegate(_Part):
    """門檻值委由授權命令的某個單元。"""

    law: str
    path: str


class Condition(_Part):
    """陳述的一個要件：角色、析取編號、概念與比較；門檻值恰有一個來源。"""

    role: str
    #: 析取編號：同一個 clause 的要件全部成立，不同 clause 之間是「或」。
    clause: int
    concept: str
    comparator: str
    threshold: Decimal | None = None
    unit: str | None = None
    value_concept: str | None = None
    negated: bool = False
    delegate: Delegate | None = None
    judgement: bool = False
    permit: str | None = None
    method: str | None = None
    averaging: str | None = None
    carried: bool | None = None


class Trigger(_Part):
    """罰則的前提：文件裡的另一個罰則（`statement`），或資料庫的陳述編號（`statement_id`）。"""

    statement: Id | None = None
    statement_id: int | None = None
    kind: str


class Sanction(_Part):
    #: 所處罰的陳述：文件裡的 id（`punishes`）或資料庫的編號（`punishes_id`）；都沒有是沒指明。
    """罰則：所處罰的陳述、效果、額度與前提。"""

    punishes: Id | None = None
    punishes_id: int | None = None
    effect: str
    amount_text: str | None = None
    min: Decimal | None = None
    max: Decimal | None = None
    repeating: bool = False
    discretionary: bool = False
    triggers: list[Trigger] | None = None


class Statement(_Part):
    """文件裡的一個陳述：錨在條文單元的原文上，附角色、要件、例外與罰則。"""

    id: Id
    law: str
    path: str
    #: 錨點：陳述所依的原文，載入時算雜湊。
    text: str
    kind: str
    deontic: str | None = None
    span: Span = None
    slots: dict[str, Slot | list[Slot]] = {}
    conditions: list[Condition] = []
    exception_of: Id | None = None
    sanction: Sanction | None = None


class Definition(_Part):
    """立法定義的一個用語：錨在定義條文上，附明文涵蓋的成員概念。"""

    id: Id
    law: str
    path: str
    text: str
    term: str
    definition: str
    members: list[str] = []
    span: Span = None


class Override(_Part):
    """陳述之間的優先關係：`winner` 優先於 `loser`。"""

    winner: Id
    loser: Id
    basis: str


class Document(_Part):
    """一份標註文件：推導、詞彙、陳述、用語定義與優先關係。"""

    derivation: Derivation
    #: 狀態預設 `extracted`；載入不接受任何陳述。
    review: str = "extracted"
    scheme: Scheme | None = None
    concepts: list[Concept] = []
    definitions: list[Definition] = []
    statements: list[Statement] = Field(default_factory=list)
    overrides: list[Override] = []
