"""查詢端的詞典：斷詞器與問句點名法規的名稱表，隨服務中的分詞器版本熱更新。

查詢要用建索引時的同一份詞典分詞，所以詞典與分詞器版本是一體的（`retrieval.tokenizer`），
對外服務哪一版由 `retrieval.serving` 決定。投影建好新版本的索引與詞頻之後才切換它；這裡的
背景執行緒輪詢它，換了就載入新版本的詞典、整份換掉。換的是不可變的快照，一個請求從頭到尾
用同一份，不會看到一半新一半舊；舊版本的索引在寬限期內都還在，所以還沒換過來的副本照常查得到。

這是設定與資料的「拉取式熱更新」：服務不必因為詞典變動而重啟，也沒有重建索引期間搜尋殘缺的時段。
"""

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from canon.law_names import LawNames
from canon.store.ports import Retrieval
from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)

#: 輪詢 `retrieval.serving` 的預設間隔（秒）。一次只讀一列；詞典與法規名稱只在版本變了才讀。
DEFAULT_REFRESH_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """一個分詞器版本的詞典：斷詞器（它的 `tokenizer_id` 就是查詢要用的版本）與法規名稱表。"""

    segmenter: Segmenter
    law_names: LawNames


class VocabularySource(Protocol):
    """端點只依賴它：取目前的詞典快照。每個請求取一次，整個請求用同一份。"""

    def current(self) -> Vocabulary:
        """目前服務中的詞典。"""
        ...


class Fixed:
    """永遠回同一份詞典的來源：測試與一次性的工具用。"""

    def __init__(self, vocabulary: Vocabulary) -> None:
        self._vocabulary = vocabulary

    def current(self) -> Vocabulary:
        """建構時給的詞典。"""
        return self._vocabulary


#: 還沒有任何服務中的分詞器（全新資料庫、投影尚未跑過）：沒有詞可查，字面臂自然沒有命中。
EMPTY = Vocabulary(Segmenter([]), LawNames({}))


def serving(retrieval: Retrieval) -> Vocabulary:
    """讀目前服務中的分詞器版本的詞典；沒有、或詞典與識別碼對不上就回 `EMPTY`。"""
    return _read(retrieval, retrieval.serving_tokenizer()) or EMPTY


def _read(retrieval: Retrieval, id_: str | None) -> Vocabulary | None:
    if id_ is None:
        return None
    segmenter = Segmenter(retrieval.tokenizer_terms(id_))
    if segmenter.tokenizer_id != id_:
        # 索引是別的分詞演算法建的（例如剛換了映像、投影還沒重建）：這個程式切不出同樣的詞，
        # 查了也只會落空。等投影以這個版本的演算法建出新版本，再換。
        log.warning("分詞器 %s 的詞典切出 %s：演算法不同，等投影重建", id_, segmenter.tokenizer_id)
        return None
    return Vocabulary(segmenter, retrieval.law_names())


class Live:
    """隨 `retrieval.serving` 更新的詞典來源；`start` 起背景輪詢，`close` 停止。"""

    def __init__(self, retrieval: Retrieval, interval: float = DEFAULT_REFRESH_SECONDS) -> None:
        self._retrieval = retrieval
        self._interval = interval
        self._vocabulary = serving(retrieval)
        #: 演算法對不上而不換的版本：記下來，免得每個間隔都重讀一次詞典。
        self._rejected: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def current(self) -> Vocabulary:
        """目前的快照；換快照是單一屬性的指派，讀的人不必加鎖。"""
        return self._vocabulary

    def refresh(self) -> bool:
        """服務中的分詞器變了就換詞典；回傳有沒有換。讀不到（資料庫暫時不可用）就維持原樣。"""
        try:
            current = self._retrieval.serving_tokenizer()
            if current in (None, self._rejected, self._vocabulary.segmenter.tokenizer_id):
                return False
            fresh = _read(self._retrieval, current)
        except Exception:
            log.warning("詞典更新失敗，維持現行版本", exc_info=True)
            return False
        if fresh is None:
            self._rejected = current
            return False
        log.info("詞典更新：%s → %s", self._vocabulary.segmenter.tokenizer_id, current)
        self._vocabulary = fresh
        return True

    def start(self) -> None:
        """起背景執行緒，每個間隔檢查一次。重複呼叫沒有作用。"""
        if self._thread is not None or self._interval <= 0:
            return
        self._thread = threading.Thread(target=self._run, name="vocabulary-refresh", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """停止輪詢；等執行緒收尾。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.refresh()
