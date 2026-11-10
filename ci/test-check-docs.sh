#!/bin/sh
# 知識文件的 front-matter 必須包在 HTML 註解裡。裸的 YAML 在 GitHub 會畫成檔首表格；
# 註解內的 `--` 會提前結束註解，所以那種寫法也要被擋下。
#   sh ci/test-check-docs.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)

python3 - "$root" <<'PY'
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("check_docs", root / "ci" / "check-docs.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

wrapped = """<!--
type: reference
applies-to: all
owns: [service-map]
-->

# 標題
"""
meta, body, commented = mod.split_front_matter(wrapped)
assert commented and meta["type"] == "reference" and body.lstrip().startswith("# 標題"), (
    meta,
    commented,
    body,
)

bare = """---
type: reference
applies-to: all
owns: [service-map]
---

# 標題
"""
meta, body, commented = mod.split_front_matter(bare)
assert meta["owns"] == "[service-map]" and not commented and body.lstrip().startswith("# 標題")
errors = mod.check_front_matter("README.md", meta, {}, commented)
assert any("HTML 註解" in e for e in errors), errors

broken = "<!--\ntype: reference\napplies-to: all\nowns: [service-map]\n---\n-->\n"
assert "--" in broken.split("-->", 1)[0]
assert not mod.skipped((".github", "SECURITY.md"))
assert not mod.skipped((".github", "ACCESSIBILITY.md"))
assert mod.skipped((".github", "pull_request_template.md"))
print("ok   front-matter 包在註解裡；裸的 YAML 被擋下")
PY
