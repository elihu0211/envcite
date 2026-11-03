"""環境部主管法規查詢系統的頁面，照真實頁面的形狀（2026-11-03 取樣）縮成測試用的最小頁。"""

from __future__ import annotations

from datetime import date

from envcite_ontology.acquire import moenv_law


def _roc(d: date) -> str:
    return f"民國 {d.year - 1911} 年 {d.month:02d} 月 {d.day:02d} 日"


def _pre(articles: list[tuple[str, str]]) -> str:
    body = "\n".join(f"<b>第 {no} 條</b>\n{text}\n" for no, text in articles)
    return f'<pre title="內容" class="csp-s0"><span class="csp-s0">{body}</pre>'


def current_page(
    title: str, amended: date, articles: list[tuple[str, str]], *, repealed: date | None = None
) -> str:
    """`repealed`：已廢止者的現行頁沒有修正日期，只有廢止日期（最後一版列在歷史清單）。"""
    stamp = (
        f"<tr><th>廢止日期：</th><td>{_roc(repealed)}</td></tr>"
        if repealed
        else f'<tr id="x"><th id="y">修正日期：</th><td>\n {_roc(amended)}\n</td></tr>'
    )
    return (
        '<html><body><div class="text-con"><table class="table table-bordered tab-edit">'
        f"<tr><th>法規名稱：</th><td>{title}"
        '<span class="label-eng"><a href="EngLawList.aspx?id=FL000001">英</a></span></td></tr>'
        f"<tr><th>公發布日：</th><td>{_roc(date(1991, 5, 23))}</td></tr>"
        f"{stamp}</table>{_pre(articles)}</div>瀏覽人數：{amended.toordinal()}</body></html>"
    )


def history_page(title: str, when: date, articles: list[tuple[str, str]]) -> str:
    return (
        '<html><body><div class="text-con"><table class="table tab-list tab-law law-content">'
        f'<tr><th width="14%">法規名稱：</th><td><a href="LawContent.aspx?id=FL000001">{title}</a>'
        "</td></tr>"
        f'<tr><th width="14%">\n<label>歷史法規：</label></th><td>\n{_roc(when)}\n</td></tr>'
        f"</table>{_pre(articles)}</div></body></html>"
    )


def history_list(site_id: str, hids: list[int]) -> str:
    rows = "".join(
        f'<tr><td>{n}.</td><td><a href="LawContentHistory.aspx?hid={h}&amp;id={site_id}">'
        "x</a></td></tr>"
        for n, h in enumerate(hids, 1)
    )
    table = f'<table class="table">{rows}</table>' if rows else ""  # 從未修正：沒有表
    return (
        '<html><body><div class="text-con"><div class="text-title">'
        f'<h2><i class="bi bi-geo-alt-fill"></i>\n 歷史法規</h2></div>{table}</div></body></html>'
    )


def categories() -> str:
    return '<a href="LawCategoryContentList.aspx?CategoryID=1601">環境綜合管理</a>'


def listing(rows: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f'<tr>\n<td class="aCenter" width="1%">{n}.</td><td class="aCenter">107.08.01</td>'
        f'<td class="aCenter"><a id="c{n}" href="LawContent.aspx?id={site_id}">{title}</a></td>'
        f'<td class="aCenter" width="10%">{kind}</td>\n</tr>'
        for n, (site_id, title, kind) in enumerate(rows, 1)
    )
    return f"<table>{body}</table>"


def site(
    site_id: str,
    title: str,
    current: tuple[date, list[tuple[str, str]]],
    past: dict[int, tuple[date, list[tuple[str, str]]]],
    *,
    kind: str = "法律",
) -> dict[str, str]:
    """網址 → 頁面。`past` 是 hid → （異動日期，條文）。"""
    pages = {
        moenv_law.CATEGORIES: categories(),
        moenv_law.LIST.format(category="1601", page=1): listing([(site_id, title, kind)]),
        moenv_law.CURRENT.format(site_id=site_id): current_page(title, *current),
        moenv_law.HISTORY_LIST.format(site_id=site_id): history_list(site_id, list(past)),
    }
    for hid, (when, articles) in past.items():
        pages[moenv_law.HISTORY.format(hid=hid, site_id=site_id)] = history_page(
            title, when, articles
        )
    return pages
