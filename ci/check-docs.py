#!/usr/bin/env python3
"""檢查文件 front-matter、相對連結與錨點；規範見容器層 AGENTS.md「知識文件」。

每份知識文件都要有 front-matter；AGENTS.md、DESIGN.md、skill、pull request 範本與評估紀錄不是知識節點，
不檢查 front-matter，連結照樣檢查。錨點比對標題 id（小寫、刪標點、空白轉連字號）。只用標準庫。
"""

import re
import sys
from pathlib import Path

TYPES = {"reference", "how-to", "tutorial", "explanation"}
APPLIES_TO = {"local", "staging", "production", "all"}
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
# 產生物與相依套件目錄，連同 worktree、快取等點開頭目錄一併略過。
SKIP_DIRS = {"node_modules", "bin", "obj", "__pycache__"}
NOT_NODES = {"AGENTS.md", "DESIGN.md", "SKILL.md"}
NOT_NODE_DIRS = ("services/ontology/eval/",)


def parse_meta_lines(lines):
    """`owns` 支援行內清單與多行清單。"""
    meta, key = {}, None
    for line in lines:
        item = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if item and key:
            meta[key] = (meta[key] + "," if meta[key] else "") + item.group(1)
            continue
        m = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*?)\s*(?:#.*)?$", line)
        if m:
            key = m.group(1)
            meta[key] = m.group(2)
    return meta


def split_front_matter(text):
    """回傳 (鍵值 dict 或 None, 內文, 是否包在 HTML 註解)。

    GitHub 把檔首以 `---` 包住的 YAML 畫成表格。知識文件改放在 HTML 註解裡，
    註解內不能出現 `--`（會提前結束註解），所以不再用 `---` 當圍欄。
    """
    lines = text.splitlines()
    if not lines:
        return None, text, False
    if lines[0].strip() == "<!--":
        for i in range(1, len(lines)):
            if lines[i].strip() == "-->":
                return parse_meta_lines(lines[1:i]), "\n".join(lines[i + 1 :]), True
        return None, text, False
    if lines[0].strip() != "---":
        return None, text, False
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return parse_meta_lines(lines[1:i]), "\n".join(lines[i + 1 :]), False
    return None, text, False


def parse_list(value):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [v.strip().strip("'\"") for v in value.split(",") if v.strip()]


def outside_fences(body):
    in_fence = False
    for line in body.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield line


def links(body):
    for line in outside_fences(body):
        yield from LINK.findall(re.sub(r"`[^`]*`", "", line))


def slug(heading):
    """標題 id：小寫、刪除標點（含全形空白），半形空白轉連字號，連續連字號併為一個。"""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "").lower()
    text = re.sub(r"[^\w\- ]", "", text.strip())
    return re.sub(r"-+", "-", text.replace(" ", "-"))


def anchors(body):
    """文件的所有標題 id；重複的標題加 -1、-2。"""
    seen = {}
    for line in outside_fences(body):
        m = HEADING.match(line)
        if m:
            base = slug(m.group(1))
            n = seen.get(base, 0)
            seen[base] = n + 1
            if n:
                seen.setdefault(f"{base}-{n}", 1)
    return set(seen)


def check_front_matter(rel, meta, owners, commented):
    errors = []
    if meta is None:
        return [f"{rel}: 缺少 front-matter（type、applies-to、owns）"]
    if not commented:
        errors.append(f"{rel}: front-matter 須包在檔首的 HTML 註解裡，否則 GitHub 會畫成表格")
    doc_type = meta.get("type")
    applies_to = meta.get("applies-to")
    types, applies = "／".join(sorted(TYPES)), "／".join(sorted(APPLIES_TO))
    if doc_type not in TYPES:
        errors.append(f"{rel}: type 須為 {types}，得到 {doc_type!r}")
    if doc_type in {"how-to", "tutorial"} and applies_to not in APPLIES_TO:
        errors.append(f"{rel}: {doc_type} 須有 applies-to（{applies}），得到 {applies_to!r}")
    elif applies_to is not None and applies_to not in APPLIES_TO:
        errors.append(f"{rel}: applies-to 須為 {applies}，得到 {applies_to!r}")
    for concept in parse_list(meta.get("owns", "")):
        if not KEBAB.match(concept):
            errors.append(f"{rel}: owns 的概念 id 須為 kebab-case：{concept!r}")
        elif concept in owners:
            errors.append(f"{rel}: 概念 {concept!r} 已被 {owners[concept]} 擁有")
        else:
            owners[concept] = rel
    return errors


def check_links(path, rel, body, ids):
    errors = []
    for target in links(body):
        if re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|//)", target):
            continue
        file_part, _, anchor = target.partition("#")
        file_part = file_part.split("?", 1)[0]
        dest = (path.parent / file_part).resolve() if file_part else path
        if not dest.exists():
            errors.append(f"{rel}: 連結目標不存在：{target}")
            continue
        if not anchor or dest.suffix != ".md":
            continue
        if dest not in ids:
            ids[dest] = anchors(split_front_matter(dest.read_text(encoding="utf-8"))[1])
        if anchor.lower() not in ids[dest]:
            errors.append(f"{rel}: 錨點不存在：{target}")
    return errors


# GitHub 社群檔放在 .github。這兩份仍是知識節點；同目錄的範本與 workflow 不是。
COMMUNITY_NODES = {(".github", "SECURITY.md"), (".github", "ACCESSIBILITY.md")}


def skipped(parts):
    if tuple(parts) in COMMUNITY_NODES:
        return False
    kept_dot_dirs = {".agents"}
    return any((x.startswith(".") and x not in kept_dot_dirs) or x in SKIP_DIRS for x in parts)


def is_node(rel):
    return Path(rel).name not in NOT_NODES and not rel.startswith(NOT_NODE_DIRS)


def main():
    root = Path(__file__).resolve().parent.parent
    files = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.md")
        if not skipped(p.relative_to(root).parts)
    )
    errors, owners, ids = [], {}, {}
    for rel in files:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        meta, body, commented = split_front_matter(text)
        if is_node(rel):
            errors += check_front_matter(rel, meta, owners, commented)
            if commented and "--" in text.split("-->", 1)[0].split("<!--", 1)[-1]:
                errors.append(f"{rel}: front-matter 註解內不能有 --，否則 GitHub 會提前結束註解")
        errors += check_links(path, rel, body, ids)
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
