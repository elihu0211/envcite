"""The set of laws a scoped retrieval measurement covers, derived from the corpus and recorded.

    uv run python tools/golden_scope.py <golden.yaml> --out eval/golden-scope-<date>.json

Three parts, each listed with the reason a law is in:

- `cited`: every law the golden set's `citation_allowlist` names (an `article_id`'s pcode, else the
  exact title), whether or not the cited article exists in the corpus;
- `authorized`: regulations whose first article says they are made under a scope law —
  「…依<法規名稱>…規定訂定」, the law's full title, in the first sentence; repeated until nothing
  is added. A relation the text does not state this way is not guessed;
- `general`: the general administrative statutes named in `GENERAL` (by exact title).

The file carries the sha256 of the sorted pcode list; the embedding tool and the evaluation read the
scope from this file, so what was embedded and what was measured are the same set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from golden_retrieval_eval import _ID, expected_of, load_corpus

from envcite_ontology.settings import DatabaseSettings, load

#: 總則法：題庫不必然引用，但行政罰與行政程序的推論要用。
GENERAL = ("行政罰法", "行政程序法")

#: 「本辦法依<法規名稱>（以下簡稱…）第…條規定訂定」。只看第一句，
#: 名稱須與某個範圍內法規的完整標題相同。
AUTHORIZED_BY = re.compile(
    r"依(?P<name>[^，。；：（]{2,40})(?:（[^）]*）)?(?:第[^，。；規]{1,20})?規定(?:訂定|授權|制定|訂之|辦理)"
)

SCOPE_RULE = {
    "cited": "題庫 citation_allowlist 所引法規（article_id 的 pcode，否則完全相同的標題）",
    "authorized": (
        "regulation，現行版第 1 條第一句符合 authorized_by_regex，"
        "且法規名稱等於範圍內法規的完整標題；逐輪至不動點"
    ),
    "general": f"完全相同標題：{'、'.join(GENERAL)}",
    "authorized_by_regex": AUTHORIZED_BY.pattern,
}


def current_laws(conn: psycopg.Connection[Any]) -> dict[str, tuple[str, str | None, str]]:
    """pcode → （現行標題, 現行版第 1 條內容, norm_kind）；未廢止者。"""
    rows = conn.execute(
        "SELECT r.local_id, e.title, v.content, r.norm_kind"
        "  FROM corpus.legal_resource AS r"
        "  JOIN corpus.legal_expression AS e"
        "    ON e.resource_id = r.id AND e.text_period @> current_date"
        "  LEFT JOIN corpus.provision AS p"
        "    ON p.resource_id = r.id AND p.provision_type = 'article' AND p.article_path = 'art_1'"
        "  LEFT JOIN corpus.provision_version AS v"
        "    ON v.provision_id = p.id AND v.superseded_at IS NULL AND v.expression_id = e.id"
        " WHERE r.id_scheme = 'moj_pcode' AND r.repealed_on IS NULL"
    )
    return {str(p): (str(t), c, str(k)) for p, t, c, k in rows}


def build(items: list[dict[str, Any]], conn: psycopg.Connection[Any]) -> dict[str, Any]:
    by_title, _ = load_corpus(conn)
    laws = current_laws(conn)
    reason: dict[str, dict[str, Any]] = {}

    for item in items:
        for exp in expected_of(item):
            if exp.article_id:
                m = _ID.match(exp.article_id)
                pcodes = {m["pcode"]} if m else set()
            else:
                pcodes = by_title.get(exp.statute, set())
            for pcode in pcodes:
                reason.setdefault(pcode, {"part": "cited"})

    for title in GENERAL:
        for pcode in by_title.get(title, set()):
            reason.setdefault(pcode, {"part": "general"})

    rounds = 0
    while True:
        titles = {laws[p][0]: p for p in reason if p in laws}
        added: dict[str, dict[str, Any]] = {}
        for pcode, (_, content, kind) in laws.items():
            if pcode in reason or not content or kind != "regulation":
                continue
            m = AUTHORIZED_BY.search(content.split("。")[0])
            parent = titles.get(m["name"].strip()) if m else None
            if parent:
                added[pcode] = {"part": "authorized", "under": parent}
        if not added:
            break
        reason.update(added)
        rounds += 1

    entries = [
        {"pcode": p, "title": laws[p][0] if p in laws else None, **why}
        for p, why in sorted(reason.items())
    ]
    pcodes = sorted(reason)
    return {
        "rule": SCOPE_RULE,
        "authorized_rounds": rounds,
        "pcodes_sha256": hashlib.sha256("\n".join(pcodes).encode()).hexdigest(),
        "counts": {
            part: sum(e["part"] == part for e in entries)
            for part in ("cited", "authorized", "general")
        }
        | {"laws": len(pcodes)},
        "not_current": [e["pcode"] for e in entries if e["title"] is None],
        "laws": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="golden_scope")
    parser.add_argument("golden", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raw = args.golden.read_bytes()
    with psycopg.connect(load(DatabaseSettings).ontology_dsn) as conn:
        scope = build(yaml.safe_load(raw)["items"], conn)
        row = conn.execute(
            "SELECT count(DISTINCT v.content_sha256) FROM corpus.provision_version AS v"
            "  JOIN corpus.legal_resource AS r ON r.id = v.resource_id"
            " WHERE v.superseded_at IS NULL AND v.content IS NOT NULL AND r.local_id = ANY(%s)",
            ([e["pcode"] for e in scope["laws"]],),
        ).fetchone()
        assert row is not None
        scope["passages"] = int(row[0])
    scope["golden_sha256"] = hashlib.sha256(raw).hexdigest()
    scope["built_on"] = date.today().isoformat()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scope, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: scope[k] for k in ("counts", "passages", "pcodes_sha256")}, indent=1))


if __name__ == "__main__":
    main()
