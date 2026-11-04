"""立法院法律系統的頁面，照真實頁面的形狀（2026-11-04 取樣）縮成測試用的最小頁。"""

from __future__ import annotations

from datetime import date

from envcite_ontology.acquire import ly_law

ACTION = "/lglawc/lglawkm?@@123"
LAW_NO = "09999"
_CN = "〇一二三四五六七八九"


def _cn(n: int) -> str:
    """1–99 的中文數字（條號用）。"""
    tens, ones = divmod(n, 10)
    head = ("" if tens == 1 else _CN[tens]) + "十" if tens else ""
    return head + (_CN[ones] if ones else "")


def _roc(d: date) -> str:
    return f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"


def _link(passed: date, session: str) -> str:
    return f"/lglawc/lawsingle?{session}000000A000000002000000^{LAW_NO}{_roc(passed)}00^00007001001"


def home() -> str:
    return (
        f'<form   method=post action="{ACTION}" name=KM >'
        "<input type=hidden name=INFO value=001C43720000210100000000000000A001>"
        '<input type=hidden name=@_1_6_T value="T_LN/LW">'
        "<input type=text name=_1_6_T size=70 maxlength=200 value=''></form>"
    )


def results(rows: list[tuple[str, date, date]], session: str = "00653B") -> str:
    body = "".join(
        "<tr class=sumtr1><td class=sumtd1>1</td><td class=sumtd2_TI>\n"
        f"<a href={_link(passed, session)} target=_blank>{title[:2]}"
        f'<font class="red">{title[2:]}</font></a>\n</td>\n<td class=sumtd2_PR>\n&nbsp;'
        f"<td class=sumtd2_PD>\n{_roc(passed)}\n</td>\n<td class=sumtd2_AD>\n{_roc(out)}\n</td>"
        for title, passed, out in rows
    )
    return f"檢索條件：({len(rows)})<table class=sumtab>{body}</table>"


def law_page(
    title: str,
    shown: date,
    versions: list[tuple[date, date]],
    articles: list[tuple[int, str]],
    session: str = "00653B",
) -> str:
    """`versions` 是（通過日，公布日），舊到新；條文是 `shown` 那一版的。"""
    history = "".join(
        "<tr><td ><table border=0 cellpadding=0 cellspacing=0 width=100%>\n"
        f"<tr><td class=version_0><a href={_link(p, session)} >中華民國{p.year - 1911}年"
        f"{p.month}月{p.day}日修正</a></td></tr>\n"
        f"<tr><td class=version_1>中華民國{o.year - 1911}年{o.month}月{o.day}日公布</td></tr>\n"
        "</table>\n</td></tr>"
        for p, o in versions
    )
    body = "".join(
        f'<tr><td><table><tr><td><nobr>&nbsp;</nobr></td><td id="part{n}">\n'
        f"<font color=C000FF>第{_cn(n)}條</font>\n"
        "<table border=0 cellpadding=0 cellspacing=0><tr><td><nobr>&nbsp;</nobr></td><td>\n"
        + "<br>\n".join(f"　　{line}" for line in text.split("\n"))
        + "\n</td></tr></table>\n</td></tr></table>\n</td></tr>"
        for n, text in articles
    )
    return (
        f"<html><table class=law_ch>{history}</table>"
        f'<td class="law_NA">{title}<span style="display:none;">({LAW_NO})</span></td>'
        f"<td valign=top class=date>中華民國{shown.year - 1911}年</td>"
        f'<table>{body}</table></td><td valign=top class="R62">條次目錄</html>'
    )


def site(
    title: str,
    versions: list[tuple[date, date, list[tuple[int, str]]]],
    *,
    session: str = "00653B",
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """（頁面，表單）：`versions` 是（通過日，公布日，條文），舊到新；最後一版是現行。"""
    dates = [(p, o) for p, o, _ in versions]
    *_, (passed, out, current) = versions
    pages = {ly_law.HOME: home()}
    first = _link(passed, session).replace("000000A", "000000B")  # 檢索結果與沿革的連結不同
    forms = {
        (ly_law.BASE + ACTION, title): results([(title, passed, out)], session).replace(
            _link(passed, session), first
        )
    }
    pages[ly_law.BASE + first] = law_page(title, passed, dates, current, session)
    for p, _, articles in versions[:-1]:
        pages[ly_law.BASE + _link(p, session)] = law_page(title, p, dates, articles, session)
    return pages, forms
