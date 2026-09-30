"""服務端的分詞器：跟著投影的現用識別碼走（env-law-docs 03-04 §5）。

投影換版時新舊識別碼的索引列並存，翻轉現用旗標之後，服務端在下一次查詢讀到新識別碼，
讀其詞典重建分詞器並換入，不需要重啟。每次查詢只多一次主鍵讀取。
"""

from __future__ import annotations

import logging
from threading import Lock

from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter

log = logging.getLogger(__name__)


class ActiveSegmenter:
    def __init__(self, store: OntologyStore) -> None:
        self._store = store
        self._lock = Lock()
        self._loaded = False
        self._active: str | None = None
        self._segmenter = Segmenter([])

    def current(self) -> Segmenter:
        active = self._store.active_tokenizer()
        if self._loaded and active == self._active:
            return self._segmenter
        with self._lock:
            if self._loaded and active == self._active:
                return self._segmenter
            segmenter = Segmenter(self._store.projection_terms(active) if active else [])
            if active and segmenter.tokenizer_id != active:
                # 分詞套件的版本與建置時不同：識別碼含實作版本，索引與查詢比不上。
                log.error(
                    "tokenizer %s cannot be reproduced here (got %s); searches will find nothing",
                    active,
                    segmenter.tokenizer_id,
                )
            self._segmenter, self._active, self._loaded = segmenter, active, True
            return segmenter
