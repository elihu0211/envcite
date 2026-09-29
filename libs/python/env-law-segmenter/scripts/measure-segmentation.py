#!/usr/bin/env python3
"""Measure how often a statutory term survives segmentation whole.

This is OOV recall in the form this corpus can supply without annotation. The
ground truth is not hand-labelled: **the statutes state what their own terms
are**, in 「本法所稱X，指…」 and in the numbered lists of 「本法專用名詞，定義
如下：一、X：指…」. Those are exactly the words a segmenter must keep whole and
exactly the words a general dictionary is least likely to hold.

Why the criterion is "survives whole" rather than a segmentation F1: a term that
comes out as two tokens is not a ranking problem. 「事業廢棄物」 split into
「事業」+「廢棄物」 makes a query for one match articles about the other, and no
amount of good ranking recovers from retrieving the wrong article.

Run it against the database once a corpus is loaded, or against a fetched
corpus before then:

    ./scripts/fetch-corpus.sh /tmp/corpus.json
    uv run scripts/measure-segmentation.py --corpus /tmp/corpus.json
    uv run scripts/measure-segmentation.py --dsn "$ONTOLOGY_DSN"
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from env_law_segmenter import Segmenter

# 「本法所稱X，指…」
P_CALLED = re.compile(r"本(?:法|辦法|條例|細則)所稱([^，：、。；]{2,12})[，：]\s*(?:指|謂|係指)")
# 「一、X：指…」 inside a definition article
P_NUMBERED = re.compile(r"[一二三四五六七八九十]+、\s*([^，：、。；]{2,12})[：:]\s*(?:指|謂|係指)")


def terms_from_text(bodies: Sequence[str]) -> list[str]:
    found: set[str] = set()
    for body in bodies:
        found.update(m.group(1).strip() for m in P_CALLED.finditer(body))
        found.update(m.group(1).strip() for m in P_NUMBERED.finditer(body))
    return sorted(found)


def load_from_file(path: Path) -> tuple[list[str], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    bodies = [body for arts in data.values() for _num, body in arts]
    return bodies, terms_from_text(bodies)


def load_from_db(dsn: str) -> tuple[list[str], list[str]]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        bodies = [
            r[0]
            for r in conn.execute(
                "SELECT corpus.article_text(v.id) FROM corpus.provision_version AS v"
                " JOIN corpus.provision AS p ON p.id = v.provision_id"
                " WHERE p.provision_type = 'article' AND v.in_force @> CURRENT_DATE"
            ).fetchall()
            if r[0] is not None
        ]
    return bodies, terms_from_text(bodies)


def occurrences(text: str, term: str) -> list[int]:
    out, i = [], text.find(term)
    while i != -1:
        out.append(i)
        i = text.find(term, i + 1)
    return out


def longest_only(text: str, terms: Sequence[str]) -> list[tuple[str, int]]:
    """Occurrences not contained in a longer term's occurrence.

    Without this the measurement is wrong in a way that looks like a result:
    「廢棄物」 inside 「一般廢棄物」 is *supposed* to vanish as a separate token,
    and counting that as a failure penalises the segmentation we want. Getting
    this wrong moved the headline number by 23 points.
    """
    hits = [(t, o) for t in terms for o in occurrences(text, t)]
    return [
        (term, off)
        for term, off in hits
        if not any(
            o2 <= off and off + len(term) <= o2 + len(t2) and (t2, o2) != (term, off)
            for t2, o2 in hits
        )
    ]


def evaluate(
    name: str,
    tokenize: Callable[[str], list[tuple[str, int, int]]],
    bodies: Sequence[str],
    terms: Sequence[str],
) -> None:
    total = kept = 0
    broken: Counter[str] = Counter()
    examples: dict[str, str] = {}
    started = time.time()
    chars = 0

    for body in bodies:
        hits = longest_only(body, terms)
        if not hits:
            continue
        tokens = tokenize(body)
        chars += len(body)
        # Offsets come from the tokenizer. Summing token lengths would be wrong
        # wherever a token was filtered out, and wrong silently.
        at = {start: word for word, start, _end in tokens}
        for term, off in hits:
            total += 1
            if at.get(off) == term:
                kept += 1
                continue
            broken[term] += 1
            if term not in examples:
                got = [w for w, s, e in tokens if e > off and s < off + len(term)]
                examples[term] = "/".join(got)

    elapsed = time.time() - started
    rate = kept / total if total else 0.0
    print(f"\n{name}")
    print(f"  術語出現 {total} 次，完整保留 {kept} 次 → {rate:.1%}")
    if elapsed > 0:
        print(f"  {chars / elapsed:,.0f} 字/秒")
    for term, n in broken.most_common(8):
        print(f"    {term} ×{n}  →  {examples.get(term, '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--corpus", type=Path, help="fetched corpus JSON")
    source.add_argument("--dsn", help="read corpus and terms from the database")
    args = parser.parse_args()

    bodies, terms = load_from_file(args.corpus) if args.corpus else load_from_db(args.dsn)
    print(f"語料 {len(bodies)} 條、{sum(len(b) for b in bodies):,} 字；法定用語 {len(terms)} 個")

    plain = Segmenter()
    evaluate("jieba（預設詞典）", plain.tokenize, bodies, terms)

    withdict = Segmenter(terms)
    evaluate(f"jieba + 語料自產詞典（{withdict.tokenizer_id}）", withdict.tokenize, bodies, terms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
