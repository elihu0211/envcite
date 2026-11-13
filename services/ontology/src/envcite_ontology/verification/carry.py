"""上位的適用情形由程式帶入：款、目與半句話的引文不靠抽取者記得上位的條件。

引文只取單元自己的字，但一個陳述的適用範圍常寫在引文之外：

- **款、目**：適用情形在上位項的導言（「在水污染管制區內，不得有下列行為：」之下的每一款）。
- **句中另起的子句**：條件在同一句的句首（「第一項公告之檢驗結果未符合…時，…應定期監測…，
  如有致危害人體健康之虞時，並應採取…」的後半句；「前項廢（污）水須經處理始能符合…者，不得…」
  只引了「不得…」）。

這兩處都能由原文的位置確定，不需要語意判斷：引文之前、同一句裡（或上位導言的助動詞之前）的
條件子句，若陳述的 scope 與主體沒有涵蓋，就由這裡補成一個 scope 要件，概念名稱是那一段原文
的字，標為承自上位（`statement_condition.carried`）。補上的是原文，抽取者寫得再少也不會漏。

主體同理：被補的那一段是行為人（詞尾是人、機關、事業…）而陳述的主體未明示，就成為它的主體。
"""

import re
from collections.abc import Mapping, Sequence
from hashlib import sha1
from typing import Any

from envcite_ontology.verification.subject import names_actor, unstated

#: 句與子句的界線。逗號不是：逗號之後仍在同一句，條件還管得到。
_BOUNDARY = "。；：:"
#: 款、目開頭的序號。
_ENUMERATOR = re.compile(
    r"^\s*(?:[一二三四五六七八九十百]+、|（[一二三四五六七八九十]+）|\([0-9一二三四五六七八九十]+\)|[0-9]+[.、])\s*"
)
#: 義務的助動詞，條件在它之前。「得」「須」只在不是詞的一部分時算（取得、獲得、須經…是條件裡的字）。
_MODAL = re.compile(r"應|不得|禁止|始可|(?<![取獲所心])得(?!以|到|知|悉)")
#: 導言：「…，不得有下列行為：」「…，應符合下列規定：」。助動詞之後的一段指向下列各款，
#: 款就是這個義務的內容；「有下列情形之一者，…」是款在當條件，款不是義務，不在此列。
_LEAD_IN = re.compile(
    r"^(?P<pre>.+?)(?:應|不得|禁止|(?<![取獲所心])得(?!以|到|知|悉))(?P<post>[^。；]*下列[^。；]*)[：:]\s*$",
    re.DOTALL,
)
#: 標點、連接詞與助詞：與 `extract._FILLER` 同義，這裡自己持有以免循環匯入。
_FILLER = re.compile(r"[，、。；：「」（）()\s]|並|及|或|與|之|其|者|時|於|以|為|應|不得")
#: 去掉填充字後至少這麼長的子句才值得帶入。
_MIN_CLAUSE = 3
#: 共同字串至少這麼長才算涵蓋（與 `extract._MIN_SHARED` 同義）。
_MIN_RUN = 2
_CONTINUATION = re.compile(r"^[，、]\s*(並|或|及|且|另)")


#: 異體字一對一歸一（污→汙），與 `extract.locate` 同義；引文在原文的位置兩邊歸一再找。
_VARIANTS = str.maketrans({"汙": "污", "巿": "市"})


def _find(quote: str, text: str) -> int:
    return text.translate(_VARIANTS).find(quote.translate(_VARIANTS)) if quote else -1


def _sentence_start(text: str, at: int) -> int:
    """`at` 所在句子的起點：上一個句界之後，款、目的序號不算。"""
    start = max((text.rfind(b, 0, at) for b in _BOUNDARY), default=-1) + 1
    found = _ENUMERATOR.match(text[start:at])
    return start + (found.end() if found else 0)


def _sentence_end(text: str, at: int) -> int:
    ends = [i for i in (text.find(b, at) for b in _BOUNDARY) if i >= 0]
    return min(ends) if ends else len(text)


def _segments(clause: str) -> list[str]:
    """一段條件文字依逗號拆成子句；指向下列各款的（「有下列情形之一」）不是條件。"""
    pieces = (p.strip("，、。；： \n") for p in re.split("[，,]", clause))
    return [p for p in pieces if p and "下列" not in p]


def head_clauses(text: str, start: int, quote: str) -> list[str]:
    """引文從句子中間起頭時，同一句句首的條件子句（助動詞之前）。

    引文自己另有主體時（「…，公私場所之所有人，不得規避」），前面的子句屬於別的行為人，不帶。
    """
    before = text[_sentence_start(text, start) : start]
    if not _FILLER.sub("", before):
        return []
    own = quote[: m.start()] if (m := _MODAL.search(quote)) else ""
    last = next((p for p in reversed(re.split("[，,]", own)) if p.strip()), "")
    if names_actor(last):
        return []
    modal = _MODAL.search(before)
    return _segments(before[: modal.start()] if modal else before)


def lead_in_clauses(context: str) -> list[str]:
    """上位項導言的條件子句：每一層導言（由外到內）助動詞之前的部分。"""
    out: list[str] = []
    for line in context.split("\n"):
        if found := _LEAD_IN.match(line.strip()):
            out += _segments(found.group("pre"))
    return out


def _covered(segment: str, labels: Sequence[str]) -> bool:
    """子句的每個字都落在某個標籤共有的連續片段（至少兩字）裡，也就是陳述已經寫了它。"""
    joined = "\n".join(labels)
    covered = [False] * len(segment)
    for i in range(len(segment)):
        n = _MIN_RUN
        while i + n <= len(segment) and segment[i : i + n] in joined:
            n += 1
        if n > _MIN_RUN:
            covered[i : i + n - 1] = [True] * (n - 1)
    rest = "".join(c for c, hit in zip(segment, covered, strict=True) if not hit)
    return len(_FILLER.sub("", rest)) < _MIN_CLAUSE


def _is_actor(segment: str) -> bool:
    """子句本身是一個行為人。「…者」是條件子句（「須經處理始能符合標準者」），不是行為人。"""
    return names_actor(segment) and not segment.endswith("者")


def concept_code(clause: str) -> str:
    """承自上位的概念代碼：同一段原文永遠是同一個概念。"""
    return "ctx_" + sha1(clause.encode()).hexdigest()[:10]


def carry(
    statements: Sequence[Mapping[str, Any]],
    text: str,
    context: str,
    labels: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """義務與禁止陳述補上上位的適用情形；回傳（新的陳述，新增的概念）。

    `labels` 是概念代碼到標籤，用來看陳述的 scope 與主體有沒有涵蓋那一段原文。
    補上的要件是成立與否的 scope，併入陳述現有 scope 的同一個 clause（且）。
    """
    lead = lead_in_clauses(context)
    out: list[dict[str, Any]] = []
    concepts: dict[str, str] = {}
    for s in statements:
        if s.get("kind") != "prescriptive" or s.get("deontic") not in ("obligation", "prohibition"):
            out.append(dict(s))
            continue
        quote = str(s.get("quote", ""))
        at = _find(quote, text)
        segments = [*lead, *(head_clauses(text, at, quote) if at >= 0 else [])]
        if s.get("slots") is not None and not isinstance(s["slots"], Mapping):
            out.append(dict(s))  # 形狀不對：留給載入器拒絕，不在這裡掩蓋
            continue
        slots = dict(s.get("slots") or {})
        conditions = [c for c in s.get("conditions") or [] if isinstance(c, Mapping)]
        named = [
            *(str(v.get("literal", "")) for v in _subject_values(slots)),
            *(
                labels.get(str(c.get("concept")), "")
                for c in conditions
                if c.get("role") == "scope"
            ),
        ]
        clauses = sorted(
            {int(c.get("clause", 1)) for c in conditions if c.get("role") == "scope"} or {1}
        )
        added: list[dict[str, Any]] = []
        for segment in dict.fromkeys(segments):
            if _covered(segment, named):
                continue
            if _is_actor(segment):
                # 上位寫的是行為人：陳述沒有主體就用它，已有主體的不再摻進別人的條件
                if unstated(slots):
                    slots["subject"] = [{"literal": segment}]
                    named.append(segment)
                continue
            code = concept_code(segment)
            concepts[code] = segment
            named.append(segment)
            # scope 有數個 clause（彼此是「或」）時，上位的條件對每一個都成立
            added += [
                {
                    "role": "scope",
                    "clause": clause,
                    "concept": code,
                    "comparator": "eq",
                    "threshold": 1,
                    "carried": True,
                }
                for clause in clauses
            ]
        changed = bool(added) or slots != (s.get("slots") or {})
        out.append(
            {**s, "slots": slots, "conditions": [*conditions, *added]} if changed else dict(s)
        )
    return out, [{"code": code, "label": label} for code, label in concepts.items()]


def _subject_values(slots: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = slots.get("subject")
    return [v for v in (raw if isinstance(raw, list) else [raw]) if isinstance(v, Mapping)]


def truncated_issues(statements: Sequence[Mapping[str, Any]], text: str) -> list[str]:
    """引文停在句中，後面接著並列的行為或替代方式（「，並…」「，或…」），而沒有任何陳述涵蓋它。

    規範句的引文要到這個義務的最後一個行為；只取一半，義務就變窄（漏了期限、替代方式）。
    只請模型修，修不好不丟：它沒有錯，只是不完整。
    """
    issues: list[str] = []
    located = [(s, _find(str(s.get("quote", "")), text)) for s in statements]
    for s, at in located:
        if s.get("kind") != "prescriptive" or at < 0:
            continue
        end = at + len(str(s.get("quote", "")))
        rest = text[end : _sentence_end(text, end)]
        if not _CONTINUATION.match(rest) or len(_FILLER.sub("", rest)) < _MIN_CLAUSE:
            continue
        covered = any(
            o is not s and at2 >= 0 and end <= at2 < end + len(rest) for o, at2 in located
        )
        if not covered:
            issues.append(
                f"陳述 {s.get('id', '?')} 的引文停在「{str(s.get('quote', ''))[-12:]}」，"
                f"同一句後面還有「{rest[:40]}」：依規則 14，引文要到這個義務的最後一個行為，"
                "並列的行為與替代方式是同一個陳述的 duty 要件。"
            )
    return issues
