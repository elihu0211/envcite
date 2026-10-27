#!/usr/bin/env python3
"""檢查文件 front-matter 與相對連結；規範見容器層 AGENTS.md「知識文件」。

只用標準庫。沒有 front-matter 的文件略過；front-matter 不含 type／applies-to／owns 任一鍵者
（如 skill 的 name／description）不屬本規範，也略過。
"""

import re
import sys
from pathlib import Path

TYPES = {"reference", "how-to", "tutorial", "explanation"}
APPLIES_TO = {"local", "staging", "production", "all"}
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 產生物與相依套件目錄，連同 worktree、快取等點開頭目錄一併略過。
SKIP_DIRS = {"node_modules", "bin", "obj", "__pycache__"}
FENCE = re.compile(r"^\s*(```|~~~)")


def split_front_matter(text):
    """回傳 (鍵值 dict 或 None, 內文)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = {}
            for line in lines[1:i]:
                m = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*?)\s*(?:#.*)?$", line)
                if m:
                    meta[m.group(1)] = m.group(2)
            return meta, "\n".join(lines[i + 1 :])
    return None, text


def parse_list(value):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [v.strip().strip("'\"") for v in value.split(",") if v.strip()]


def links(body):
    in_fence = False
    for line in body.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = re.sub(r"`[^`]*`", "", line)
        yield from LINK.findall(line)


def main():
    root = Path(__file__).resolve().parent.parent
    files = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.md")
        if not any(x.startswith(".") and x not in {".agents", ".gitlab"} or x in SKIP_DIRS for x in p.relative_to(root).parts)
    )
    errors = []
    owners = {}

    for rel in files:
        path = root / rel
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        if meta is not None and {"type", "applies-to", "owns"} & meta.keys():
            doc_type = meta.get("type")
            applies_to = meta.get("applies-to")
            if doc_type not in TYPES:
                errors.append(f"{rel}: type 須為 {'／'.join(sorted(TYPES))}，得到 {doc_type!r}")
            if doc_type in {"how-to", "tutorial"} and applies_to not in APPLIES_TO:
                errors.append(f"{rel}: {doc_type} 須有 applies-to（{'／'.join(sorted(APPLIES_TO))}），得到 {applies_to!r}")
            elif applies_to is not None and applies_to not in APPLIES_TO:
                errors.append(f"{rel}: applies-to 須為 {'／'.join(sorted(APPLIES_TO))}，得到 {applies_to!r}")
            for concept in parse_list(meta.get("owns", "")):
                if not KEBAB.match(concept):
                    errors.append(f"{rel}: owns 的概念 id 須為 kebab-case：{concept!r}")
                elif concept in owners:
                    errors.append(f"{rel}: 概念 {concept!r} 已被 {owners[concept]} 擁有")
                else:
                    owners[concept] = rel

        for target in links(body):
            if re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|#|//)", target):
                continue
            target_path = target.split("#", 1)[0].split("?", 1)[0]
            if target_path and not (path.parent / target_path).resolve().exists():
                errors.append(f"{rel}: 連結目標不存在：{target}")

    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
