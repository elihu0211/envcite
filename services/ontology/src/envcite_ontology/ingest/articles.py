"""條文切分。

層級依中央法規標準法第 8 條，靠行首編號辨識：無編號為項，「一、」款，「（一）」目，
「1.」目之 1；縮排行與表格框線是上一單元的續行，其他編號（如 `(1)`）不切。

序位不連續或下層無上層時不猜，整條退回為單一單元、全文保留，並回報原因。
"""

import re
from dataclasses import dataclass, field

from envcite_ontology.ingest.numerals import CN_NUMERALS, parse_int

_ARTICLE_NO = re.compile(r"^第\s*([0-9]+(?:\s*-\s*[0-9]+)*)\s*條$")
_BARE_NO = re.compile(r"^([0-9]+(?:-[0-9]+)*)$")

_CN = re.escape(CN_NUMERALS)
_SUBPARAGRAPH = re.compile(rf"^([{_CN}]+)、")
_ITEM = re.compile(rf"^（([{_CN}]+)）")
_SUBITEM = re.compile(r"^([0-9]+)[.．](?![0-9])")
_CONTINUATION = re.compile(r"^[ \t　│├┌└┬┴┼─┐┘┤]")
_DELETED = re.compile(r"^[（(]刪除[）)]$")

PROVISION_TYPES = {
    "art": "article",
    "para": "paragraph",
    "pt": "subparagraph",
    "item": "item",
    "sub": "subitem",
}


@dataclass(frozen=True)
class Unit:
    """切出的一個條文單元：路徑、層級類型與文字。"""

    path: str
    type: str
    #: 單元自己的文字；沒有則為 None，例如條下切出了項時，條本身沒有文字。
    content: str | None


@dataclass(frozen=True)
class Split:
    """一條切分的結果：單元清單，與切不乾淨時的原因。"""

    units: list[Unit]
    # 切不乾淨的原因；None 表示照法定層級切成。
    degraded: str | None = None


def article_path(article_no: str) -> str | None:
    """`第 5-1 條` 或 `5-1` → `art_5-1`；認不得的寫法回 None。"""
    m = _ARTICLE_NO.match(article_no.strip()) or _BARE_NO.match(article_no.strip())
    if m is None:
        return None
    return "art_" + "-".join(str(int(n)) for n in re.split(r"\s*-\s*", m.group(1)))


def is_deleted(content: str) -> bool:
    """內容是否為「（刪除）」標記，即該條已被刪除。"""
    return _DELETED.match(content.strip()) is not None


def sort_key(path: str) -> list[int]:
    """閱讀順序的整數陣列（0001 provision.sort_key）：下層單元為上層的鍵加 0 再加序位。"""
    head, *rest = path.split("/")
    key = [int(n) for n in head.removeprefix("art_").split("-")]
    for segment in rest:
        key += [0, int(segment.rsplit("_", 1)[1])]
    return key


def split(article: str, content: str) -> Split:
    """把一條的文字切成單元。`article` 是 `art_N` 路徑。"""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    try:
        return Split(_split(article, content))
    except _Inconsistent as e:
        # 文字照原樣保留，只去掉縮排與空行，與切成單元時的續行處理一致。
        text = "\n".join(line.strip() for line in content.split("\n") if line.strip())
        return Split([Unit(article, "article", text or None)], degraded=str(e))


class _Inconsistent(Exception):
    pass


@dataclass
class _Node:
    path: str
    lines: list[str]
    children: list[_Node]


def _expect(kind: str, m: re.Match[str], parent: _Node, label: str) -> _Node:
    try:
        n = int(m.group(1)) if m.group(1).isdigit() else parse_int(m.group(1))
    except ValueError:
        raise _Inconsistent(f"{label}序位無法解讀：{m.group(1)}") from None
    if n != len(parent.children) + 1:
        raise _Inconsistent(f"{label}序位不連續：{parent.path} 之下出現第 {n} 個")
    node = _Node(f"{parent.path}/{kind}_{n}", [], [])
    parent.children.append(node)
    return node


# 項之下的層級，由上而下。
_LEVELS = (
    (_SUBPARAGRAPH, "pt", "款"),
    (_ITEM, "item", "目"),
    (_SUBITEM, "sub", "目之 1"),
)


@dataclass
class _Outline:
    """逐行建樹的狀態：已出現的項，與目前所在的路徑。"""

    article: str
    paragraphs: list[_Node] = field(default_factory=list)
    # 由上而下目前所在的路徑：[項, 款, 目, 目之 1]。
    stack: list[_Node] = field(default_factory=list)

    def add(self, line: str) -> None:
        """放進一行（已去空白、不是續行）：有層級編號的接在上層之下，否則另起一項。"""
        for depth, (pattern, kind, label) in enumerate(_LEVELS, start=1):
            m = pattern.match(line)
            if m is None:
                continue
            if depth == 1 and not self.stack:
                # 條以款起首：項沒有導引文字，仍是第 1 項。
                self.paragraphs.append(_Node(f"{self.article}/para_1", [], []))
                self.stack[:] = [self.paragraphs[0]]
            if len(self.stack) < depth:
                raise _Inconsistent(f"{label}出現在沒有上層的位置")
            node = _expect(kind, m, self.stack[depth - 1], label)
            node.lines.append(line)
            del self.stack[depth:]
            self.stack.append(node)
            return
        node = _Node(f"{self.article}/para_{len(self.paragraphs) + 1}", [line], [])
        self.paragraphs.append(node)
        self.stack[:] = [node]


def _paragraphs(article: str, content: str) -> list[_Node]:
    outline = _Outline(article)
    for raw in content.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if outline.stack and _CONTINUATION.match(line):
            outline.stack[-1].lines.append(line.strip() if line[0] in " \t　" else line)
            continue
        outline.add(line.strip())
    return outline.paragraphs


def _emit(units: list[Unit], node: _Node) -> None:
    kind = node.path.rsplit("/", 1)[1].split("_")[0]
    units.append(Unit(node.path, PROVISION_TYPES[kind], "\n".join(node.lines) or None))
    for child in node.children:
        _emit(units, child)


def _split(article: str, content: str) -> list[Unit]:
    paragraphs = _paragraphs(article, content)
    if not paragraphs:
        raise _Inconsistent("沒有文字")

    if len(paragraphs) == 1 and not paragraphs[0].children:
        return [Unit(article, "article", "\n".join(paragraphs[0].lines))]

    units = [Unit(article, "article", None)]
    for p in paragraphs:
        _emit(units, p)
    return units
