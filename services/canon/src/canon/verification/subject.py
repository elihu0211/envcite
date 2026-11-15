"""主體槽位的確定性檢查：義務人必須是原文寫出的行為人，否則記成「未明示」。

原文省略義務人（被動式、主題句、代名詞）時，抽取者取字面上的主語，常是被規範的設施、藥劑或行為
（「飲用水設備，應符合國家標準」的「飲用水設備」），或憑空補一個「任何人」「行為人」。這兩種都不是
原文的義務人。判斷不靠提示、不靠審查者：一個主體值要同時滿足兩條，才留下。

1. **有根據**：它的字由單元（含上層）原文裡的片段拼成。原文沒有的字是抽取者補的。
2. **是行為人**：以人、者、機關、事業、單位這類字結尾；否則它要在引文裡有一段行為接在後面
   （「建築物污水處理設施，排放廢（污）水於地面水體者，應…」），因為接著助動詞的物是被規範的對象；
   或在原文裡與另一個行為人並列（「事業、污水下水道系統及建築物污水處理設施」是法律列舉的受規範者）。

不合的值整個丟掉；一個也不剩，主體記為未明示。這是正規化，不是駁回：陳述本身仍交給驗證。
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: 異體字一對一歸一，與 `checks.locate.locate` 同義（污→汙），這裡自己持有以免循環匯入。
_VARIANTS = str.maketrans({"汙": "污", "巿": "市"})

#: 行為人名詞的詞尾。「設施」「系統」不在其中：它們可以是行為人，也可以是被規範的物，
#: 要看後面有沒有接著行為（`_acts`）。
_ACTOR_TAIL = re.compile(
    r"(人|者|員|業|機關|機構|單位|政府|場所|廠|公司|團體|組織|"
    r"部|署|局|處|會|院|所|系統)$"
)
#: 括號內的附註不影響詞尾（「事業或污水下水道系統（不含…）」）。
_TRAILING_NOTE = re.compile(r"[（(][^（()）]*[）)]\s*$")
#: 原文用助動詞標出義務的位置。
_MODAL = re.compile(r"應|不得|得|須|禁止|始可")
#: 主體與助動詞之間的標點與連接：這些不算接在後面的行為。
_GLUE = re.compile(r"[，、。；：,\s]|及|或|與|和|並")
#: 接在主體後面的行為至少這麼長（去掉標點與連接詞後）才算。
_MIN_ACT = 3
#: 「屬」「為」「有」這類繫詞或存在詞開頭的不是行為：說的是物的狀態（「基金來源屬追繳之所得」）。
_NOT_AN_ACT = re.compile(r"^(屬|為|係|有|無|含|包括|在|於|依)")
#: 並列主體之間的連接字。
_JOIN = "、或及與和"
#: 兩個字的片段才算由原文拼成：單字幾乎處處相同。
_MIN_RUN = 2
#: 抽取者偶爾直接把「沒有」寫成值。
_NONE_WORDS = frozenset({"未明示", "未明確", "不明", "無", "unstated"})


def _norm(text: str) -> str:
    return text.translate(_VARIANTS)


def grounded(literal: str, text: str) -> bool:
    """`literal` 的每個字都落在與 `text` 共有的連續片段（至少兩字）裡。

    結尾的「者」例外：抽取者常把「使用農藥或化學肥料」寫成「…者」，前面的字有根據就算。
    """
    lit, source = _norm(literal), _norm(text)
    if len(lit) > _MIN_RUN and lit.endswith("者"):
        lit = lit[:-1]
    if not lit:
        return False
    covered = [False] * len(lit)
    for i in range(len(lit)):
        n = _MIN_RUN
        while i + n <= len(lit) and lit[i : i + n] in source:
            n += 1
        if n > _MIN_RUN:
            covered[i : i + n - 1] = [True] * (n - 1)
    return all(covered)


def names_actor(literal: str) -> bool:
    """字面以行為人名詞結尾（括號附註不計）。"""
    return bool(_ACTOR_TAIL.search(_TRAILING_NOTE.sub("", literal.strip())))


def _acts(literal: str, quote: str) -> bool:
    """引文裡，主體字面與第一個助動詞之間還隔著一段行為（不只是標點與連接詞）。"""
    modal = _MODAL.search(quote)
    at = _norm(quote).find(_norm(literal))
    if modal is None or at < 0 or at + len(literal) > modal.start():
        return False
    between = _GLUE.sub("", quote[at + len(literal) : modal.start()])
    return len(between) >= _MIN_ACT and not _NOT_AN_ACT.match(between)


def _coordinated(literal: str, peers: Sequence[str], source: str) -> bool:
    """原文裡，`literal` 與某個行為人並列（只隔著頓號、或、及）。"""
    text, mine = _norm(source), re.escape(_norm(literal))
    glue = f"[{_JOIN}]+"
    for peer in peers:
        if peer == literal or not names_actor(peer):
            continue
        other = re.escape(_norm(peer))
        if re.search(f"{mine}{glue}{other}|{other}{glue}{mine}", text):
            return True
    return False


def acceptable(literal: str, quote: str, source: str, peers: Sequence[str] = ()) -> str | None:
    """主體值不合的原因；合格為 None。

    `source` 是單元原文加上層條文，供檢查有沒有根據；`peers` 是同一個主體槽位的所有擇一值。
    """
    if literal.strip() in _NONE_WORDS:
        return "says it is unstated"
    if not grounded(literal, source):
        return "is not written in the text"
    if names_actor(literal) or _acts(literal, quote) or _coordinated(literal, peers, source):
        return None
    return "is the regulated thing, not the one who must act"


def _values(slot: object) -> list[Mapping[str, Any]]:
    items = slot if isinstance(slot, list) else [slot]
    return [v for v in items if isinstance(v, Mapping)]


def unstated(slots: Mapping[str, Any]) -> bool:
    """槽位表已把主體寫成「未明示」。"""
    return any(v.get("unstated") is True for v in _values(slots.get("subject")))


def normalize(
    statements: Sequence[Mapping[str, Any]], text: str, context: str
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """義務與禁止陳述的主體正規化；回傳（新的陳述，id → 被丟掉的值與原因）。

    其餘陳述原樣不動。主體留下的值保留原本的責任基礎；沒有一個值合格（或根本沒寫）就記成未明示。
    """
    source = f"{context}\n{text}"
    out: list[dict[str, Any]] = []
    dropped: dict[str, list[str]] = {}
    for s in statements:
        if s.get("kind") != "prescriptive" or s.get("deontic") not in ("obligation", "prohibition"):
            out.append(dict(s))
            continue
        raw = s.get("slots")
        if raw is not None and not isinstance(raw, Mapping):
            out.append(dict(s))  # 形狀不對：留給載入器拒絕，不在這裡掩蓋
            continue
        slots = dict(raw) if raw is not None else {}
        quote = str(s.get("quote", ""))
        kept: list[Mapping[str, Any]] = []
        values = [v for v in _values(slots.get("subject")) if v.get("unstated") is not True]
        peers = [str(v.get("literal") or "") for v in values]
        for v in values:
            literal = str(v.get("literal") or "")
            reason = acceptable(literal, quote, source, peers)
            if reason is None:
                kept.append(v)
            else:
                dropped.setdefault(str(s.get("id", "?")), []).append(f"{literal}: {reason}")
        slots["subject"] = kept if kept else {"unstated": True}
        out.append({**s, "slots": slots})
    return out, dropped
