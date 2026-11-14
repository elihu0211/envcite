"""ASP 事實的寫法：字串常數與千分位整數。"""

from decimal import Decimal


def q(text: str) -> str:
    """ASP 字串常數。"""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def milli(value: Decimal | float | int) -> int:
    """數值轉成千分位整數；先轉成 `Decimal`，不帶入浮點誤差。"""
    return round(Decimal(str(value)) * 1000)
