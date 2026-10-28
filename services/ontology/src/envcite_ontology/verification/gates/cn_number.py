"""讀中文與阿拉伯數字、金額及其限定詞（以上、以下）；數字與引用閘門共用。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

#: 全國法規資料庫的零多寫成「○」（U+25CB），不是「〇」（U+3007）。
_DIGIT = {c: i for i, c in enumerate("〇一二三四五六七八九")} | {"零": 0, "○": 0}
_UNIT = {"十": 10, "百": 100, "千": 1000}
NUMBER = "0-9,〇○零一二三四五六七八九十百千萬億"
_FRACTION = "0-9〇○零一二三四五六七八九"


def cn_number(s: str) -> int:
    """「六萬」→ 60000、「二千萬」→ 20000000、「一百」→ 100、「二○○」→ 200；讀不出拋 ValueError。"""
    s = s.replace(",", "").replace(" ", "")
    if not s:
        raise ValueError(s)
    if s.isdigit():
        return int(s)
    if all(ch in _DIGIT for ch in s):  # 沒有位數字的是逐位寫法：「二○○」→ 200
        return int("".join(str(_DIGIT[ch]) for ch in s))
    for mark, scale in (("億", 10**8), ("萬", 10**4)):
        if mark in s:
            head, _, tail = s.partition(mark)
            return (cn_number(head) if head else 1) * scale + (cn_number(tail) if tail else 0)
    total = cur = 0
    for ch in s:
        if ch in _DIGIT:
            cur = _DIGIT[ch]
        elif ch in _UNIT:
            total += (cur or 1) * _UNIT[ch]
            cur = 0
        else:
            raise ValueError(s)
    return total + cur


_MONEY = re.compile(rf"([{NUMBER}]+)元(以上|以下)?")


def amounts(text: str) -> list[tuple[int, str | None]]:
    """金額及其限定詞，依出現順序；讀不出的略過。"""
    out: list[tuple[int, str | None]] = []
    for m in _MONEY.finditer(text):
        try:
            out.append((cn_number(m.group(1)), m.group(2)))
        except ValueError:
            continue
    return out


#: 帶比較詞的數值：「十八歲以上」「三十日以內」，與前置的「未滿」「未達」；
#: 數字與詞之間容許一段單位。
_AFTER = re.compile(rf"([{NUMBER}]+)[^{NUMBER}，。；：、（）()\s]{{0,6}}?(以上|以下|以內)")
_BEFORE = re.compile(rf"(未滿|未達)([{NUMBER}]+)")

#: 以上、以下、以內含本數（刑法第 10 條第 1 項，他法從之），未滿、未達不含；
#: 「逾」「超過」無明文，不判定。
QUALIFIER_COMPARATOR = {"以上": "ge", "以下": "le", "以內": "le", "未滿": "lt", "未達": "lt"}


def qualified_numbers(text: str) -> list[tuple[int, str]]:
    """原文中帶比較詞（以上、以下、以內、未滿、未達）的整數及其詞，依出現順序。"""
    found: list[tuple[int, int, str]] = []
    for m in _AFTER.finditer(text):
        try:
            found.append((m.start(), cn_number(m.group(1)), m.group(2)))
        except ValueError:
            continue
    for m in _BEFORE.finditer(text):
        try:
            found.append((m.start(), cn_number(m.group(2)), m.group(1)))
        except ValueError:
            continue
    return [(value, word) for _, value, word in sorted(found)]


def ranges(found: Sequence[tuple[int, str | None]]) -> list[tuple[int | None, int | None]]:
    """把「以上」「以下」配成（下限, 上限）；只有一邊的另一邊為 None。"""
    out: list[tuple[int | None, int | None]] = []
    low: int | None = None
    for value, qualifier in found:
        if qualifier == "以上":
            if low is not None:
                out.append((low, None))
            low = value
        elif qualifier == "以下":
            out.append((low, value))
            low = None
    if low is not None:
        out.append((low, None))
    return out


def numbers_in(text: str) -> set[Decimal]:
    """所有讀得出的數，含小數（「○．一」→ 0.1；小數點可為「.」「．」「點」，小數部分逐位讀）。"""
    found: set[Decimal] = set()
    for m in re.finditer(rf"([{NUMBER}]+)(?:[.．點]([{_FRACTION}]+))?", text):
        try:
            whole = cn_number(m.group(1))
        except ValueError:
            continue
        digits = m.group(2) or ""
        fraction = "".join(c if c.isdigit() else str(_DIGIT[c]) for c in digits)
        found.add(Decimal(f"{whole}.{fraction}") if fraction else Decimal(whole))
    return found
