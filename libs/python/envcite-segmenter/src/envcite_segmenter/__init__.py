"""以語料衍生之 lexicon 進行的中文斷詞。

索引端與查詢端必須使用同一個 segmenter。不一致並不會拋出例外：查詢的 lexeme 與
索引中的不同，比對到的列變少，結果清單是變短而不是出錯——這是更難察覺的失敗。
本套件的存在，就是讓兩端無法取用到不同的實作。

做法：lexicon 內的用語由左至右取最長者，整個保留；其餘漢字切成相鄰的二字組（bigram），
英數字連續成一個詞（小寫）。沒有第三方依賴，也沒有通用詞典——法定用語是 lexicon 給的，
其餘文字用二字組，兩端必然切出相同的結果。理由與量測見 README.md 與
`scripts/measure-segmentation.py`。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence

__all__ = ["ALGORITHM", "Segmenter", "lexicon_digest", "tokenizer_id"]

# 切法改變時遞增：它進 tokenizer id，既有索引因此被判定為過期並重建。
ALGORITHM = "lexbigram-1"


def lexicon_digest(terms: Iterable[str]) -> str:
    """lexicon 內容的穩定短摘要。

    先排序並去除重複，讓摘要取決於 lexicon 中有哪些用語，而不是它們進來的順序——
    否則重新執行匯入程式，看起來就像換了 tokenizer。
    """
    joined = "\n".join(sorted({t for t in terms if t}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def tokenizer_id(terms: Iterable[str]) -> str:
    """寫入 `retrieval.lexical_index.tokenizer` 的值。

    帶有決定斷詞結果的兩半：實作（`ALGORITHM`）與 lexicon。任一者改變，先前建立索引的
    tsvector 就無法與新的查詢比較，而偵測這件事正是該欄位存在的目的。
    """
    return f"{ALGORITHM}+dict-{lexicon_digest(terms)}"


def _is_han(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF  # CJK 統一表意文字
        or 0x3400 <= code <= 0x4DBF  # 擴充 A
        or 0x20000 <= code <= 0x2FA1F  # 擴充 B 以後與相容補充
        or 0xF900 <= code <= 0xFAFF  # 相容表意文字
    )


def _is_word(ch: str) -> bool:
    """半形與全形的英數字：連續者成一個詞。"""
    return (
        (ch.isascii() and ch.isalnum())
        or "０" <= ch <= "９"
        or "Ａ" <= ch <= "Ｚ"
        or "ａ" <= ch <= "ｚ"
    )


class Segmenter:
    """給定相同的 lexicon，每次都以相同方式斷詞；不持有任何共享的可變狀態。"""

    def __init__(self, terms: Sequence[str] = ()) -> None:
        self._terms = tuple(sorted({t for t in terms if t}))
        self._lexicon = frozenset(self._terms)
        self._longest = max((len(t) for t in self._terms), default=0)

    @property
    def tokenizer_id(self) -> str:
        return tokenizer_id(self._terms)

    @property
    def terms(self) -> tuple[str, ...]:
        return self._terms

    def _longest_term_at(self, text: str, i: int) -> int:
        """從 i 起最長的 lexicon 用語長度；沒有為 0。"""
        for length in range(min(self._longest, len(text) - i), 0, -1):
            if text[i : i + length] in self._lexicon:
                return length
        return 0

    def tokenize(self, text: str) -> list[tuple[str, int, int]]:
        """詞及其字元位移：(word, start, end)。

        位移在切的當下記錄，而不是事後把 token 長度加總重建：只要有任何字元被濾掉
        （空白、標點），重建就會出錯，而且出錯是無聲的。
        """
        tokens: list[tuple[str, int, int]] = []
        han_start = -1  # 目前尚未成詞的連續漢字起點

        def flush_han(end: int) -> None:
            nonlocal han_start
            if han_start < 0:
                return
            if end - han_start == 1:
                tokens.append((text[han_start], han_start, end))
            else:
                tokens.extend((text[k : k + 2], k, k + 2) for k in range(han_start, end - 1))
            han_start = -1

        i, n = 0, len(text)
        while i < n:
            length = self._longest_term_at(text, i)
            if length:
                flush_han(i)
                tokens.append((text[i : i + length], i, i + length))
                i += length
            elif _is_han(text[i]):
                if han_start < 0:
                    han_start = i
                i += 1
            elif _is_word(text[i]):
                flush_han(i)
                j = i
                while j < n and _is_word(text[j]):
                    j += 1
                tokens.append((text[i:j].lower(), i, j))
                i = j
            else:
                flush_han(i)
                i += 1
        flush_han(n)
        return tokens

    def segment(self, text: str) -> list[str]:
        """詞；空白與標點不成詞。"""
        return [word for word, _start, _end in self.tokenize(text)]

    def to_tsvector_input(self, text: str) -> str:
        """以空格分隔的詞，可直接交給 `to_tsvector('simple', …)`。

        資料庫從不斷詞——它收到的就是詞。這裡是唯一決定何謂一個詞的地方。
        """
        return " ".join(self.segment(text))
