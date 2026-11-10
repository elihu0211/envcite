"""政府網站的 HTML。頁面常缺引號或缺結束標籤，用 lxml 的回復模式解析。

可見文字把每個標籤當成空白，`<br>` 兩側的字才會分開。`keep_lines` 只把 `<br>` 換成換行，
其餘標籤拿掉、不補空白：函釋內文是一行一項。法條句式的正規式不在這裡。
"""

from __future__ import annotations

from lxml import html
from lxml.html import HtmlElement

_PARSER = html.HTMLParser(recover=True, encoding="utf-8")


def parse(page: str) -> HtmlElement:
    """整頁。回復模式接受沒有引號的屬性。空字串是一頁沒有節點的文件，不是解析失敗。"""
    if not page.strip():
        return html.Element("html")
    return html.fromstring(page, parser=_PARSER)


def visible_text(source: str, *, keep_lines: bool = False) -> str:
    """標籤內的可見文字。預設把標籤當空白再收成一行；`keep_lines` 保留換行與 `<br>`。"""
    if not source.strip():
        return ""
    root = html.fragment_fromstring(source, create_parent="div", parser=_PARSER)
    parts: list[str] = []

    def walk(node: HtmlElement) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            if keep_lines:
                if child.tag == "br":
                    parts.append("\n")
            else:
                parts.append(" ")
            walk(child)
            if not keep_lines:
                parts.append(" ")
            if child.tail:
                parts.append(child.tail)

    walk(root)
    text = "".join(parts).replace("\xa0", " ")
    if keep_lines:
        return text
    return " ".join(text.split())
