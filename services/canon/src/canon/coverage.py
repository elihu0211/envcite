"""查詢詞的覆蓋率：一條條文含有多少比例的查詢詞。

字面臂是各詞的 OR，只含一個詞的條文也會入選，分數與含全部詞的差不多；語料裡沒有任何一條含完整
詞組時，這些片段命中反而排在最前面。覆蓋率另外量這件事，不改字面分數與名次：字面分數的尺度是
consult 的相關性下界（`Relevance`）校準的對象，乘上覆蓋率會讓整個尺度下移，可答題的通過率在原下界
下大幅下降，而可答與無答案的取捨沒有改善（開發集量測）。
"""

from collections.abc import Sequence

from envcite_segmenter import Segmenter


def term_coverage(query_terms: Sequence[str], text: str, segmenter: Segmenter) -> float:
    """`query_terms`（去重）中，`text` 以同一個分詞器切出來也有的比例；沒有查詢詞為 0。"""
    wanted = set(query_terms)
    if not wanted:
        return 0.0
    return len(wanted & set(segmenter.segment(text))) / len(wanted)
