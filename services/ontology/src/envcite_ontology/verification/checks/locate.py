"""引文在原文的定位：兩邊把異體字一併歸一，位置取自原文。"""

#: 異體字一對一歸一（位置不變）：全國法規資料庫寫「污」「巿」，模型常寫成「汙」「市」。
VARIANTS = str.maketrans({"汙": "污", "巿": "市"})


def locate(quote: str, text: str) -> int:
    """引文在原文的起點，異體字視為同字；不在原文（或空引文）為 -1。"""
    return text.translate(VARIANTS).find(quote.translate(VARIANTS)) if quote else -1
