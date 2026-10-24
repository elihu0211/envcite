"""Hold-out slices for the extraction A/B: build them from the MOJ law export.

    python tools/ab_slices.py <dir containing law.zip>

Same slice rule as tools/holdout.py (first six fine articles plus the articles they cite), but read
from the export directly instead of a development database.
"""

import json
import re
import sys
from pathlib import Path

from envcite_ontology.acquire.moj import load_laws, pcode
from envcite_ontology.ingest import articles as art_mod
from envcite_ontology.verification.gates import citations

S = Path(sys.argv[1])
_, laws = load_laws(S / "law.zip")
FINE = re.compile(r"處新臺幣.{0,40}罰鍰")
names = {"空氣污染防制法", "噪音管制法", "廢棄物清理法", "海洋污染防治法"}
for law in laws:
    if law["LawName"] not in names or law.get("LawLevel") != "法律":
        continue
    pc = pcode(law)
    arts = {}
    for a in law["LawArticles"]:
        if a.get("ArticleType") != "A":
            continue
        m = re.search(r"第\s*(\d+)(?:-(\d+))?\s*條", a["ArticleNo"])
        key = "art_" + (m.group(1) + ("_" + m.group(2) if m.group(2) else "")) if m else None
        if key:
            arts[key] = a
    fined = [k for k, a in arts.items() if FINE.search(a["ArticleContent"])][:6]
    cited = {re.sub(r"/.*", "", x) for k in fined for x in citations(arts[k]["ArticleContent"])}
    picked = [k for k in arts if k in set(fined) | cited]
    left = [k for k in picked if art_mod.split(k, arts[k]["ArticleContent"]).degraded]
    picked = [k for k in picked if k not in left]
    out = dict(law)
    out["LawArticles"] = [arts[k] for k in picked]
    (S / f"{pc}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(
        pc,
        law["LawName"],
        "articles",
        len(arts),
        "fined",
        fined,
        "picked",
        len(picked),
        "left out",
        left,
    )
