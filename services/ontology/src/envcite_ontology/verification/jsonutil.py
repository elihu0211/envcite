"""模型的回答常把 JSON 包在 ```json 圍欄或前後的說明裡：取出第一個 JSON 物件。"""

import json
from typing import Any


def object_from(text: str) -> dict[str, Any] | None:
    """從模型的回答取出第一個 JSON 物件；取不出為 None。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
