"""以語料衍生之 lexicon 進行的中文斷詞。

索引端與查詢端必須使用同一個 segmenter。不一致並不會拋出例外：查詢的 lexeme 與
索引中的不同，比對到的列變少，結果清單是變短而不是出錯——這是更難察覺的失敗。
本套件的存在，就是讓兩端無法取用到不同的實作。

用哪一個 segmenter、以及為什麼，是量測出來的而非假設的：見 README.md 與
`scripts/measure-segmentation.py`。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from importlib.metadata import version

import jieba

__all__ = ["Segmenter", "lexicon_digest", "tokenizer_id"]


def lexicon_digest(terms: Iterable[str]) -> str:
    """lexicon 內容的穩定短摘要。

    先排序並去除重複，讓摘要取決於 lexicon 中有哪些用語，而不是它們進來的順序——
    否則重新執行匯入程式，看起來就像換了 tokenizer。
    """
    joined = "\n".join(sorted({t for t in terms if t}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def tokenizer_id(terms: Iterable[str]) -> str:
    """寫入 `subdivision.tokenizer` 的值。

    帶有決定斷詞結果的兩半：實作與 lexicon。任一者改變，先前建立索引的 tsvector
    就無法與新的查詢比較，而偵測這件事正是該欄位存在的目的。
    """
    return f"jieba-{version('jieba')}+dict-{lexicon_digest(terms)}"


class Segmenter:
    """給定相同的 lexicon，每次都以相同方式斷詞。

    持有自己的 `jieba.Tokenizer`，而不使用模組層級的預設實例：預設實例是
    process 全域的可變狀態，若有第二個元件往裡面加詞，就會悄悄改變本元件的輸出。
    """

    def __init__(self, terms: Sequence[str] = ()) -> None:
        self._terms = tuple(sorted({t for t in terms if t}))
        self._tokenizer = jieba.Tokenizer()
        self._tokenizer.initialize()
        for term in self._terms:
            # 詞頻設得夠高，讓該用語絕不會被拆成它的組成部分。法規把它定義為
            # 一個整體；segmenter 沒有理由不同意。
            self._tokenizer.add_word(term, freq=100_000)

        # 已知且經量測的限制：jieba 的 `re_han` 不包含全形括號，因此無論詞典
        # 怎麼寫，它都無法產出跨越括號的 token。「廢（污）水處理設施」與
        # 「自然保護（育）區」正是這樣書寫的法定用語，而它們就是全部的殘餘錯誤——
        # 1036 次中的 11 次，也就是 98.9% 之外剩下的 1.1%。註冊不含括號的變體
        # 毫無作用，因為被斷詞的文字本身帶有括號；本檔案的較早版本就這麼做過，
        # 看起來像是處理了這個情況。
        #
        # 修正它意味著覆寫 jieba 模組層級的 `re_han_default`，而那正是本類別
        # 刻意避開的 process 全域狀態。在這 1.1% 值得那樣取捨之前，暫且不處理。

    @property
    def tokenizer_id(self) -> str:
        return tokenizer_id(self._terms)

    @property
    def terms(self) -> tuple[str, ...]:
        return self._terms

    def tokenize(self, text: str) -> list[tuple[str, int, int]]:
        """詞及其字元位移：(word, start, end)。

        位移取自 jieba，而不是把 token 長度加總來重建。只要有任何 token 被濾掉，
        重建就會出錯——`segment()` 對空白正是這麼做的——而且出錯是無聲的：第一個
        被丟棄的 token 之後，每個位置都是錯的，於是比對位置的檢查程式會把正確的
        斷詞回報為失敗。這件事在這裡發生過，讓一個主要數字偏移了三十個百分點。
        """
        return list(self._tokenizer.tokenize(text))

    def segment(self, text: str) -> list[str]:
        """詞，去除空白。

        空白之所以丟棄而不保留為 token，是因為輸出在下游會以空格串接；保留它會
        產生空的 lexeme。需要位置時請用 `tokenize()`。
        """
        return [w for w in self._tokenizer.cut(text) if w.strip()]

    def to_tsvector_input(self, text: str) -> str:
        """以空格分隔的詞，可直接交給 `to_tsvector('simple', …)`。

        資料庫從不斷詞——它收到的就是詞。這裡是唯一決定何謂一個詞的地方。
        """
        return " ".join(self.segment(text))
