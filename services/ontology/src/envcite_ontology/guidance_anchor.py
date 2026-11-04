"""行政規則的錨定：法規名稱 → pcode、條號 → 發文日現行的最細單元（02-05 §3.3、§7.2）。

**名稱解析**讀 `guidance.law_name`（遷移 0050）：語料任一版本的標題與沿革記載的舊名，名稱是鍵；
對到兩部以上法規的名稱沒有 pcode，回歧義而不取第一筆——猜錯的代價是把函釋錨到另一部法規上，
而這種錯誤不會報錯。唯一性由視圖的定義保證，這裡不重做。

名稱比對只正規化全形半形與空白（02-05 §3.2），與 `guidance.name_key` 相同。舊名只在這裡解析：
檢索的法名比對另有自己的名稱表（`law_names`），兩者不共用，以免檢索的範圍規則與錨定的唯一性
規則互相牽動。

**錨定**：條號對到條文單元，展開為該單元之下每個最細單元在發文日現行的文字；發文日沒有現行版本的
不錨定，不以現行文字代替。

**內文的依據**（公報的行政規則沒有相關法規欄）：只取緊接在「核釋」「依」「依據」「為執行」之後、
寫成「法名第幾條」的引用，法名須是解析得到的名稱；其餘提到的條號不一定是被解釋或所依據的，不取。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import psycopg

from envcite_ontology.acquire import moenv
from envcite_ontology.ingest import numerals

Conn = psycopg.Connection[tuple[object, ...]]
Problem = tuple[str, str, str]  # （severity，problem_code，detail）

#: 內文中寫出依據的字樣，緊接在法名之前（引號與空白已去掉）：解釋的對象，或訂定的依據
_INTERPRETS = re.compile(r"核釋$")
_BASIS = re.compile(r"(?:依據|依照|為執行|依)$")
#: 「依…」只在說明本規則怎麼訂出來的那一句才是依據（「特訂定本要點」「依…規定訂定之」）；
#: 其餘的「依…規定」是規則的內容，「訂定協議書」「授權訂定之自治法規」也不是本規則的訂定
_ENACTS = re.compile(r"[特爰]訂定|訂定本|訂定之$")
#: 句子以句號、分號分開（NFKC 之後全形分號成了半形）
_SENTENCE = re.compile(r"[^。;]+")
#: 同一個依據字樣下並列的下一部法規：`依甲法第一條及乙法第二條`
_AND = re.compile(r"(?:規定)?[及、暨與和]")
_N = rf"(?:\d+|[{numerals.CN_NUMERALS}]+)"
_ARTICLE = re.compile(rf"第{_N}(?:-\d+)?條")
_ARTICLE_PATH = re.compile(rf"第{_N}(?:-\d+)?條(?:之{_N})?(?:第{_N}項)?(?:第{_N}款)?(?:第{_N}目)?")
_QUOTES = str.maketrans("", "", "「」『』")
#: 引文往前看多遠找依據字樣
_BASIS_WINDOW = 4


@dataclass(frozen=True, slots=True)
class Resolved:
    """一個名稱的解析結果：pcode，或未解析的原因。"""

    pcode: str | None
    problem: str | None


class Resolver:
    """一次同步讀一次語料的標題與更名紀錄；對到的法規以 pcode 表示。"""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn
        rows = conn.execute("SELECT name, pcode, alias FROM guidance.law_name").fetchall()
        conn.rollback()
        self._names = {str(n): None if p is None else str(p) for n, p, _ in rows}
        self._aliases = sum(1 for *_, alias in rows if alias)
        self._longest = max(map(len, self._names), default=0)

    @property
    def aliases(self) -> int:
        """解析得到的舊名數（不含與標題相同者）。"""
        return self._aliases

    def resolve(self, law_name: str) -> Resolved:
        key = name_key(law_name)
        if key not in self._names:
            return Resolved(None, "law_unresolved")
        pcode = self._names[key]
        return Resolved(pcode, None if pcode else "law_ambiguous")

    def anchors(
        self, cited: str, on: date, c: moenv.Citation | None = None
    ) -> tuple[list[dict[str, str]], list[Problem]]:
        """一處引用（如 `空氣污染防制法第32條第1項`）→ 載入文件的 `interprets`，或對不上的原因。

        已分好法名與條號的（`basis` 的結果；法名本身可能含「第幾條」）由 `c` 給。
        """
        c = c or moenv.citation(cited)
        resolved = self.resolve(c.law_name)
        if resolved.pcode is None:
            assert resolved.problem is not None
            return [], [("warning", resolved.problem, cited)]
        if c.path is None:
            return [], [("warning", "no_article", cited)]
        rows = self._conn.execute(
            """
            SELECT p.path, v.content
              FROM corpus.provision AS p
              JOIN corpus.legal_resource AS r ON r.id = p.resource_id
              LEFT JOIN corpus.provision_version AS v
                ON v.provision_id = p.id AND v.superseded_at IS NULL AND v.content IS NOT NULL
               AND v.force_period @> %(on)s::date
             WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %(pcode)s
               AND (p.path = %(path)s OR starts_with(p.path, %(path)s || '/'))
             ORDER BY p.sort_key
            """,
            {"pcode": resolved.pcode, "path": c.path, "on": on},
        ).fetchall()
        if not rows:
            return [], [("warning", "provision_unresolved", cited)]
        found = [
            {"law": resolved.pcode, "path": str(path), "text": str(text)}
            for path, text in rows
            if text is not None
        ]
        if not found:
            return [], [("warning", "no_version_at_issue", f"{cited}（{on}）")]
        return found, []

    def basis(self, body: str) -> list[tuple[str, moenv.Citation]]:
        """內文寫出依據的引用（原文，法名與條號），依出現順序、去重；法名須解析得到。"""
        text = "".join(unicodedata.normalize("NFKC", body).translate(_QUOTES).split())
        found: dict[str, moenv.Citation] = {}
        for sentence in _SENTENCE.findall(text):
            enacts = _ENACTS.search(sentence) is not None
            last = -1  # 上一處取用的引文結束的位置
            for m in _ARTICLE.finditer(sentence):
                name = self._name_before(sentence, m.start())
                if name is None:
                    continue
                at = m.start() - len(name)
                lead = sentence[max(0, at - _BASIS_WINDOW) : at]
                chained = last >= 0 and _AND.fullmatch(sentence[last:at]) is not None
                if not (_INTERPRETS.search(lead) or (enacts and _BASIS.search(lead)) or chained):
                    continue
                path = _cited_path(sentence, m.start())
                last = m.start() + len(path)
                article = moenv.citation(f"甲{path}").path
                if article is not None:
                    found.setdefault(name + path, moenv.Citation(name, article))
        return list(found.items())

    def _name_before(self, text: str, end: int) -> str | None:
        """緊接在 `end` 之前、最長的一個已知名稱。"""
        for length in range(min(self._longest, end), 2, -1):
            name = text[end - length : end]
            if self.resolve(name).problem != "law_unresolved":
                return name
        return None


def name_key(title: str) -> str:
    """名稱比對只正規化全形半形與空白（02-05 §3.2）。"""
    return "".join(unicodedata.normalize("NFKC", title).split())


def _cited_path(text: str, start: int) -> str:
    """引文中「第幾條」起、到條號序列結束的原文（`第七條之一第二項`）。"""
    m = _ARTICLE_PATH.match(text, start)
    assert m is not None
    return m.group(0)
