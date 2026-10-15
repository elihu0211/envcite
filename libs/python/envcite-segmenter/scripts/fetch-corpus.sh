#!/usr/bin/env bash
# 抓幾部環境法規當評估語料（政府公開資料）。
#
# 用 curl 而不是 Python：law.moj.gov.tw 的憑證缺 Subject Key Identifier，Python 的驗證器拒絕、
# curl 接受；關掉驗證不是解法，是另一個問題。
set -euo pipefail

out="${1:?usage: fetch-corpus.sh <output.json>}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for pcode in O0020001 O0040001 O0050001 O0090001; do
  curl -fsS "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=$pcode" \
    -H "User-Agent: Mozilla/5.0" --max-time 30 -o "$tmp/$pcode.html"
done

python3 - "$tmp" "$out" <<'PY'
import html, json, re, sys
from pathlib import Path

LAWS = {
    "O0020001": "空氣污染防制法",
    "O0040001": "水污染防治法",
    "O0050001": "廢棄物清理法",
    "O0090001": "環境影響評估法",
}
src, dest = Path(sys.argv[1]), Path(sys.argv[2])

def articles(page: str) -> list[tuple[str, str]]:
    t = re.sub(r"<script.*?</script>", "", page, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    parts = re.split(r"第\s*(\d+(?:-\d+)?)\s*條", t)
    out = []
    for i in range(1, len(parts) - 1, 2):
        body = " ".join(parts[i + 1].split())
        if 10 < len(body) < 3000:
            out.append((parts[i], body))
    return out

corpus = {}
for pcode, name in LAWS.items():
    arts = articles((src / f"{pcode}.html").read_text(encoding="utf-8", errors="replace"))
    corpus[name] = arts
    print(f"{name}: {len(arts)} 條", file=sys.stderr)

dest.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
PY
echo "→ $out"
