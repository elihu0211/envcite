"""抽取者與驗證者必須是不同的模型：溫度 0 下以探測問題比對。"""

from collections.abc import Sequence

from envcite_ontology.verification.models import Model

#: 溫度 0 下逐字相同的回答，幾乎必定是同一個模型的兩個名字。
PROBES = ("用兩句話說明什麼是法律的位階，要有你自己的措辭。", "寫一首四句的詩，主題是河川。")


class SameModel(ValueError):
    """兩個名字背後是同一個模型，不獨立，不能互相驗證。"""


def assert_distinct(models: Sequence[Model]) -> None:
    """對每個模型問同樣的開放式問題，逐字相同即同一模型（託管服務常有別名）。

    只抓得到完全相同的別名，抓不到同基礎模型的微調；僅在溫度為 0 的轉接器上有效。
    """
    answers = {m.name: tuple(m.complete(p) for p in PROBES) for m in models}
    names = list(answers)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if answers[a] == answers[b]:
                raise SameModel(
                    f"{a} and {b} answered every probe identically: same model under two names"
                )
