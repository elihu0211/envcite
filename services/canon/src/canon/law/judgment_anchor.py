"""從裁判理由抽出所適用的條文，並判定錨定的版本（02-05 §7.3 階段二、三）。

只取「適用」「依」「違反」句型裡、緊接在已知名稱之後的條號；「不適用」不是適用。同一句若在比較或援引
（比較、參照、對照、有別、類推、援引），整句不取。名稱走行政規則的解析器（`guidance.law_name`），
同名歧義不取第一筆。條號展開到有文字的最細單元，與函釋同一套。

版本不猜。句中沒有「修正前」「行為時」「裁處時」時，錨裁判日現行的文字，`basis` 為 `decided_on`。
有這些用語時只在對得上唯一版本才錨，`basis` 為 `stated`：「修正前」是裁判日現行版本的前一版；
「行為時」「裁處時」須同一句裡恰有一個日期，且該日恰有一個版本。對不上的引用不產生錨定，
裁判本身仍可入庫。

改抽取或版本規則須遞增 `RULE_VERSION`。`Resolver` 讀完名稱表會 rollback，所以 `extract` 不要
放在還有未提交寫入的交易裡。
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal, NamedTuple

from canon import numerals
from canon.db import Conn, fetch
from canon.import_problems import Problem
from canon.law import moenv
from canon.law.guidance_anchor import Resolver

#: 抽取或版本規則的版本；改變輸出時遞增。
RULE_VERSION = "1"

_QUOTES = str.maketrans("", "", "「」『』")
_SENTENCE = re.compile(r"[^。;]+")
_AND = re.compile(r"(?:規定)?[及、暨與和]")
_N = rf"(?:\d+|[{numerals.CN_NUMERALS}]+)"
_ARTICLE = re.compile(rf"第{_N}(?:-\d+)?條")
_ARTICLE_PATH = re.compile(rf"第{_N}(?:-\d+)?條(?:之{_N})?(?:第{_N}項)?(?:第{_N}款)?(?:第{_N}目)?")
#: 「不適用」「非依」是否定，不是適用句型；版本用語可以夾在「依」與法名之間
_LEAD = re.compile(
    r"(?:(?<![不非])適用|違反|(?<![不非])(?:依據|依照|依))(?:修正前|行為時|裁處時)*之?$"
)
#: 適用句型後面跟著名稱表沒有的法名
_UNKNOWN = re.compile(
    r"(?:(?<![不非])適用|違反|(?<![不非])(?:依據|依照|依))"
    r"(?:修正前|行為時|裁處時)*之?(?P<name>[^第]{2,40})$"
)
_LEAD_WINDOW = 12
_COMPARE = re.compile(r"比較|參照|對照|有別|類推|援引")
_CONDUCT = re.compile(r"行為時|裁處時")
_ARABIC_DATE = re.compile(r"(?<!\d)(\d{2,3})年(\d{1,2})月(\d{1,2})日")
_ROC_YEAR_MAX = 200

_Mode = Literal["decided_on", "prior", "conduct", "conflict"]

#: 有文字的單元在某日的現行文字；母條沒有文字，不入結果。沒有現行文字的列 content 為 NULL。
_ON_DATE = """
SELECT p.path, v.content
  FROM corpus.provision AS p
  JOIN corpus.legal_resource AS r ON r.id = p.resource_id
  LEFT JOIN corpus.provision_version AS v
    ON v.provision_id = p.id AND v.superseded_at IS NULL AND v.content IS NOT NULL
   AND v.force_period @> %(on)s::date
 WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %(pcode)s
   AND (p.path = %(path)s OR starts_with(p.path, %(path)s || '/'))
   AND EXISTS (
       SELECT 1 FROM corpus.provision_version AS any_v
        WHERE any_v.provision_id = p.id AND any_v.superseded_at IS NULL
          AND any_v.content IS NOT NULL)
 ORDER BY p.sort_key
"""

#: 某日現行版本的前一版。texts 不是恰一個元素，就是對不上唯一版本。
_PREDECESSOR = """
SELECT p.path, array_agg(prev.content) AS texts
  FROM corpus.provision AS p
  JOIN corpus.legal_resource AS r ON r.id = p.resource_id
  JOIN corpus.provision_version AS cur
    ON cur.provision_id = p.id AND cur.superseded_at IS NULL AND cur.content IS NOT NULL
   AND cur.force_period @> %(on)s::date
  LEFT JOIN corpus.provision_version AS prev
    ON prev.provision_id = p.id AND prev.superseded_at IS NULL AND prev.content IS NOT NULL
   AND upper(prev.force_period) = lower(cur.force_period)
 WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %(pcode)s
   AND (p.path = %(path)s OR starts_with(p.path, %(path)s || '/'))
 GROUP BY p.path, p.sort_key
 ORDER BY p.sort_key
"""


@dataclass(frozen=True, slots=True)
class Extraction:
    """`extract` 的結果：可寫入 `judgment.load` 的引用，與未錨定的原因。"""

    cites: tuple[dict[str, str], ...]
    problems: tuple[Problem, ...]


def extract(conn: Conn, body: str, decided_on: date) -> Extraction:
    """理由 → 錨定用的引用。對不上的引用只出現在 `problems`，不猜測版本。"""
    resolver = Resolver(conn)
    cites: list[dict[str, str]] = []
    problems: list[Problem] = []
    seen: set[tuple[str, str, str, str]] = set()
    for sentence in _SENTENCE.findall(_normalize(body)):
        if _COMPARE.search(sentence) and _ARTICLE.search(sentence):
            problems.append(Problem("warning", "comparative_mention", sentence))
            continue
        mode = _mode(sentence)
        applied, unknown = _applicative(resolver, sentence)
        for name, path in unknown:
            problems.append(Problem("warning", "law_unresolved", name + path))
        for name, cited in applied:
            _one(conn, resolver, name, cited, decided_on, mode, sentence, cites, seen, problems)
    return Extraction(tuple(cites), tuple(problems))


def _one(
    conn: Conn,
    resolver: Resolver,
    name: str,
    cited: moenv.Citation,
    decided_on: date,
    mode: _Mode,
    sentence: str,
    cites: list[dict[str, str]],
    seen: set[tuple[str, str, str, str]],
    problems: list[Problem],
) -> None:
    label = f"{name}{cited.path}"
    resolved = resolver.resolve(name)
    if resolved.pcode is None or cited.path is None:
        problems.append(Problem("warning", resolved.problem or "no_article", label))
        return
    placed = _place(conn, resolved.pcode, cited.path, decided_on, mode, sentence)
    if isinstance(placed, str):
        problems.append(Problem("warning", placed, label))
        return
    for path, text, basis in placed:
        key = (resolved.pcode, path, text, basis)
        if key in seen:
            continue
        seen.add(key)
        cites.append({"law": resolved.pcode, "path": path, "text": text, "basis": basis})


def _place(
    conn: Conn,
    pcode: str,
    path: str,
    decided_on: date,
    mode: _Mode,
    sentence: str,
) -> list[tuple[str, str, str]] | str:
    """引用展開後的（路徑，原文，依據）；對不上時回問題代碼。"""
    if mode == "conflict":
        return "version_not_unique"
    if mode == "conduct":
        dates = _dates(sentence)
        if len(dates) != 1:
            return "version_not_unique"
        return _on_date(conn, pcode, path, dates[0], "stated", "version_not_unique")
    if mode == "prior":
        return _prior(conn, pcode, path, decided_on)
    return _on_date(conn, pcode, path, decided_on, "decided_on", "no_version_on_decision")


class _TextRow(NamedTuple):
    path: str
    content: str | None


class _PriorRow(NamedTuple):
    path: str
    #: 前一版的文字；不只一段（前一版不唯一）就不採用。
    texts: list[str | None]


def _on_date(
    conn: Conn, pcode: str, path: str, on: date, basis: str, missing: str
) -> list[tuple[str, str, str]] | str:
    rows = fetch(conn, _ON_DATE, {"pcode": pcode, "path": path, "on": on}, _TextRow)
    if not rows:
        return "provision_unresolved"
    found = [(row.path, row.content, basis) for row in rows if row.content is not None]
    return found if len(found) == len(rows) else missing


def _prior(conn: Conn, pcode: str, path: str, on: date) -> list[tuple[str, str, str]] | str:
    rows = fetch(conn, _PREDECESSOR, {"pcode": pcode, "path": path, "on": on}, _PriorRow)
    found = [
        (row.path, text, "stated")
        for row in rows
        if len(row.texts) == 1 and (text := row.texts[0]) is not None
    ]
    return found if rows and len(found) == len(rows) else "version_not_unique"


def _applicative(
    resolver: Resolver, sentence: str
) -> tuple[list[tuple[str, moenv.Citation]], list[tuple[str, str]]]:
    """句中的適用引用，以及名稱表沒有的法名。並列的條號沿用前一個法名。"""
    found: dict[str, tuple[str, moenv.Citation]] = {}
    unknown: list[tuple[str, str]] = []
    last = -1
    previous: str | None = None
    for match in _ARTICLE.finditer(sentence):
        name = resolver.name_before(sentence, match.start())
        chained = _chained(sentence, last, match.start(), name, previous)
        if name is None and not chained:
            missed = _UNKNOWN.search(sentence[: match.start()])
            if missed is not None:
                unknown.append((missed.group("name"), _cited_path(sentence, match.start())))
            continue
        if name is not None and not _led(sentence, match.start() - len(name)):
            previous = None
            last = -1
            continue
        previous = name or previous
        cited = _cited_path(sentence, match.start())
        last = match.start() + len(cited)
        article = moenv.citation(f"甲{cited}").path
        if previous is None or article is None:
            continue
        found.setdefault(previous + cited, (previous, moenv.Citation(previous, article)))
    return list(found.values()), unknown


def _chained(sentence: str, last: int, start: int, name: str | None, previous: str | None) -> bool:
    return (
        name is None
        and previous is not None
        and last >= 0
        and _AND.fullmatch(sentence[last:start]) is not None
    )


def _led(sentence: str, at: int) -> bool:
    return _LEAD.search(sentence[max(0, at - _LEAD_WINDOW) : at]) is not None


def _mode(sentence: str) -> _Mode:
    prior = "修正前" in sentence
    conduct = _CONDUCT.search(sentence) is not None
    if prior and conduct:
        return "conflict"
    if prior:
        return "prior"
    if conduct:
        return "conduct"
    return "decided_on"


def _dates(sentence: str) -> list[date]:
    """句中的民國日期（中文數字或阿拉伯數字），去重、保留出現順序。"""
    found: list[date] = []
    for match in numerals.DATE_BARE.finditer(sentence):
        if (parsed := numerals.roc_date(*match.groups())) is not None:
            found.append(parsed)
    for match in _ARABIC_DATE.finditer(sentence):
        year, month, day = (int(group) for group in match.groups())
        if not 1 <= year <= _ROC_YEAR_MAX:
            continue
        try:
            found.append(date(year + 1911, month, day))
        except ValueError:
            continue
    return list(dict.fromkeys(found))


def _cited_path(text: str, start: int) -> str:
    matched = _ARTICLE_PATH.match(text, start)
    assert matched is not None
    return matched.group(0)


def _normalize(body: str) -> str:
    return "".join(unicodedata.normalize("NFKC", body).translate(_QUOTES).split())
