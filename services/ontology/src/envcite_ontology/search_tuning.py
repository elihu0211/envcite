"""檢索的調校旋鈕與並行名額：組合根（`server`）從設定組出，注入 servicer。"""

from dataclasses import dataclass

#: 請求帶 `rerank` 時，兩臂預設取幾條候選交給精排模型（`ServerSettings.rerank_candidates` 覆寫）。
DEFAULT_RERANK_CANDIDATES = 30


@dataclass(frozen=True, slots=True)
class SearchTuning:
    """檢索的旋鈕；預設值是不調校（測試直接建構、不走伺服器預算時）。"""

    #: 字面臂略過高頻詞的段落比例上限；0 為不略過。
    lexical_max_df: float = 0.0
    #: 函釋入口容許的命中函釋占比上限；0 為不限制。
    guidance_max_df: float = 0.0
    #: 精排看多深：兩臂取這麼多條候選再精排。
    rerank_candidates: int = DEFAULT_RERANK_CANDIDATES
    #: 同時向嵌入端點取查詢向量的上限，滿了就略過語意臂；None 為不限。
    embed_slots: int | None = None
    #: 同時向精排端點打分的上限，滿了就退回精排前的次序；None 為不限。
    rerank_slots: int | None = None
