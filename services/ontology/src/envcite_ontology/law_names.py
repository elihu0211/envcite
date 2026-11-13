"""問句點名的法規。

問句裡出現法規的全名（「水污染防治法第26條…」）時，檢索只在那些法規裡找：全庫的候選池被其他
法規稀釋，期望條文常落在前 50 條之外。名稱取自資料庫所有版本的標題，所以舊名稱也認得。
"""

from collections.abc import Mapping, Sequence

#: 二字的名稱（民法、刑法）常是別的詞的一部分，不當作點名。
MIN_TITLE_CHARS = 3


class LawNames:
    def __init__(self, pcodes_by_title: Mapping[str, Sequence[str]]) -> None:
        self._pcodes = {
            t: tuple(p) for t, p in pcodes_by_title.items() if len(t) >= MIN_TITLE_CHARS
        }
        self._longest_first = sorted(self._pcodes, key=len, reverse=True)

    def find(self, query: str) -> list[str]:
        """問句點名的法規代碼（排序、不重複）。名稱由長到短比對且不重疊：點名「水污染防治法施行
        細則」就不再算點名「水污染防治法」。"""
        taken = [False] * len(query)
        found: set[str] = set()
        for title in self._longest_first:
            start = query.find(title)
            while start >= 0:
                end = start + len(title)
                if not any(taken[start:end]):
                    taken[start:end] = [True] * len(title)
                    found.update(self._pcodes[title])
                    break
                start = query.find(title, start + 1)
        return sorted(found)
