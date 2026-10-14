"""中文數字與民國日期。

法規沿革以「中華民國一百零七年八月一日」這類寫法記日期。自己逐位解析而不用通用套件：
原文有「一百三年」（= 103 年，省略了「零」），通用轉換套件讀成 130，而且不會報錯。這裡以
「百」後只剩一位數字即個位數處理，並靠呼叫端以來源另給的日期交叉核對。
"""

from __future__ import annotations

import re
from datetime import date

_DIGIT = {c: i for i, c in enumerate("〇一二三四五六七八九")} | {"零": 0}
_EXPAND = {"廿": "二十", "卅": "三十"}

#: 可出現在中文數字裡的字元，供組正規式用。
CN_NUMERALS = "〇零一二三四五六七八九十百廿卅"
_N = f"[{CN_NUMERALS}]+"

_ERA = r"(?:中華)?民國"
DATE_WITH_ERA = re.compile(rf"{_ERA}\s*({_N})\s*年\s*({_N})\s*月\s*({_N})\s*日")
DATE_BARE = re.compile(rf"(?:{_ERA})?\s*({_N})\s*年\s*({_N})\s*月\s*({_N})\s*日")

_ROC_OFFSET = 1911


def _one(c: str) -> int:
    if len(c) != 1 or c not in _DIGIT:
        raise ValueError(c)
    return _DIGIT[c]


def _below_hundred(s: str) -> int:
    if "十" in s:
        tens, _, ones = s.partition("十")
        return (_one(tens) if tens else 1) * 10 + (_one(ones) if ones else 0)
    return _one(s)


def parse_int(s: str) -> int:
    """「一百零七」→ 107、「一百十二」→ 112、「一百三」→ 103、「二十」→ 20；讀不出拋 ValueError。"""
    s = "".join(_EXPAND.get(c, c) for c in s.replace(" ", ""))
    if not s or any(c not in _DIGIT and c not in "十百" for c in s):
        raise ValueError(s)
    if "百" in s:
        head, _, rest = s.partition("百")
        hundreds = _one(head) if head else 1
        if not rest:
            return hundreds * 100
        if rest[0] in "〇零":
            tail = rest[1:]
            return hundreds * 100 + (_below_hundred(tail) if tail else 0)
        if "十" in rest:
            return hundreds * 100 + _below_hundred(rest)
        return hundreds * 100 + _one(rest)
    if "十" in s:
        return _below_hundred(s)
    return int("".join(str(_DIGIT[c]) for c in s))


def roc_date(year: str, month: str, day: str) -> date | None:
    """民國年月日（中文數字）→ 西元 date；讀不出或不是真的日期為 None。"""
    try:
        return date(_ROC_OFFSET + parse_int(year), parse_int(month), parse_int(day))
    except ValueError:
        return None


def first_date(text: str, *, era_required: bool = True) -> date | None:
    """text 中第一個可讀的民國日期。era_required=False 時接受省略「中華民國」的寫法。"""
    pattern = DATE_WITH_ERA if era_required else DATE_BARE
    for m in pattern.finditer(text):
        if (d := roc_date(*m.groups())) is not None:
            return d
    return None


def parse_yyyymmdd(s: str) -> date | None:
    """來源的 `20140122` 形式；空字串或不合法為 None。"""
    if not re.fullmatch(r"\d{8}", s or ""):
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None
