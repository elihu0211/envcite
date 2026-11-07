"""已確認的義務裡，確定性規則找得到的兩型瑕疵（唯讀）。

    uv run python tools/duty_defects.py <dsn> <agent-version> [<agent-version> …]

- **主體**：主體值不是原文寫出的行為人（`subject.acceptable`），或義務沒有主體欄。新抽取已把這些記成
  「未明示」，所以新版本應該是 0；舊版本的數字就是基線。
- **適用範圍**：引文之前、同一句或上位導言的條件，陳述的 scope 與主體沒涵蓋（`carry.carry`
  會補的）。新版本的條件已經帶入，應該是 0。
- **引文截斷**：引文停在句中，後面接著並列的行為或替代方式，沒有任何已確認的陳述涵蓋
  （`carry.truncated_issues`）。這型程式補不了（要抽取者把行為寫進 duty），只送回修復，所以新舊
  版本都可能有。

規則和寫入前用的是同一份程式，所以這支工具量的是「寫入前的規則擋下了多少」（舊版本）與
「規則之外還剩什麼」（新版本）；語意上對不對由 `duty_audit.py` 量。
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Any

import psycopg

from envcite_ontology.verification.carry import carry, truncated_issues
from envcite_ontology.verification.subject import acceptable, normalize

DUTY = "s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')"


def _context(conn: Any, provision_id: int) -> str:
    return "\n".join(
        str(r[0])
        for r in conn.execute(
            "WITH RECURSIVE up(id, parent_id, depth) AS ("
            " SELECT p.id, p.parent_id, 0 FROM corpus.provision p WHERE p.id = %s"
            " UNION ALL SELECT p.id, p.parent_id, up.depth + 1"
            "   FROM corpus.provision p JOIN up ON p.id = up.parent_id)"
            " SELECT DISTINCT ON (up.depth) v.content FROM up"
            "  JOIN corpus.provision_version v ON v.provision_id = up.id"
            "   AND v.superseded_at IS NULL"
            " WHERE up.depth > 0 AND v.content IS NOT NULL AND btrim(v.content) <> ''"
            " ORDER BY up.depth DESC, v.id DESC",
            (provision_id,),
        )
    )


def _has(conn: Any, table: str, column: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'annotation'"
            " AND table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        is not None
    )


def measure(conn: Any, version: str) -> dict[str, Any]:
    unstated = "sl.unstated" if _has(conn, "statement_slot", "unstated") else "false"
    carried = "c.carried" if _has(conn, "statement_condition", "carried") else "false"
    rows = conn.execute(
        "SELECT s.id, s.provision_id, v.content, lower(s.span), upper(s.span),"
        "       s.norm_modality::text, a.confirm_state::text"
        "  FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
        "  JOIN annotation.derivation d ON d.id = a.derivation_id"
        "  JOIN corpus.provision_version v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256"
        f" WHERE {DUTY} AND d.producer_version = %s ORDER BY s.id",
        (version,),
    ).fetchall()
    stats: dict[str, int] = defaultdict(int)
    confirmed_in: dict[int, list[dict[str, Any]]] = defaultdict(list)
    texts: dict[int, str] = {}
    statements: list[tuple[str, dict[str, Any], str, str]] = []
    for sid, pid, text, lo, hi, modality, state in rows:
        subjects = conn.execute(
            f"SELECT coalesce(k.name, sl.literal), {unstated} FROM annotation.statement_slot sl"
            " LEFT JOIN vocab.concept k ON k.id = sl.concept_id"
            " WHERE sl.statement_id = %s AND sl.slot_role = 'subject' ORDER BY sl.alternative",
            (sid,),
        ).fetchall()
        conds = conn.execute(
            f"SELECT c.condition_role::text, k.name, {carried}"
            " FROM annotation.statement_condition c JOIN vocab.concept k ON k.id = c.concept_id"
            " WHERE c.statement_id = %s ORDER BY c.id",
            (sid,),
        ).fetchall()
        quote = str(text) if lo is None else str(text)[lo:hi]
        statement: dict[str, Any] = {
            "id": str(sid),
            "kind": "prescriptive",
            "deontic": modality,
            "quote": quote,
            "slots": (
                {"subject": [{"unstated": True} if u else {"literal": str(n)} for n, u in subjects]}
                if subjects
                else {}
            ),
            "conditions": [
                {"role": r, "concept": str(n), "clause": 1, "carried": bool(cr)}
                for r, n, cr in conds
            ],
        }
        context = _context(conn, int(pid))
        if state == "confirmed":
            confirmed_in[int(pid)].append(statement)
        texts[int(pid)] = str(text)
        statements.append((state, statement, str(text), context))
    for state, statement, text, context in statements:
        if state != "confirmed":
            continue
        stats["confirmed"] += 1
        values = (statement.get("slots") or {}).get("subject") or []
        named = [v["literal"] for v in values if "literal" in v]
        if not values:
            stats["subject_absent"] += 1
        elif not named:
            stats["subject_unstated"] += 1
        elif any(acceptable(n, statement["quote"], f"{context}\n{text}", named) for n in named):
            stats["subject_refused"] += 1
        fixed, _ = normalize([statement], text, context)
        labels = {str(c["concept"]): str(c["concept"]) for c in statement["conditions"]}
        out, _ = carry(fixed, text, context, labels)
        before = sum(1 for c in statement["conditions"] if c.get("carried"))
        after = sum(1 for c in out[0]["conditions"] if c.get("carried"))
        stats["carried"] += before > 0
        if after > before or out[0].get("slots") != fixed[0].get("slots"):
            stats["context_missing"] += 1
    for pid, group in confirmed_in.items():
        stats["truncated"] += len(truncated_issues(group, texts[pid]))
    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dsn")
    parser.add_argument("versions", nargs="+")
    args = parser.parse_args()
    keys = (
        "confirmed",
        "subject_refused",
        "subject_absent",
        "subject_unstated",
        "carried",
        "context_missing",
        "truncated",
    )
    print("| 版本 | " + " | ".join(keys) + " |\n|---|" + "---|" * len(keys))
    with psycopg.connect(args.dsn) as conn:
        for version in args.versions:
            m = measure(conn, version)
            print(f"| {version} | " + " | ".join(str(m.get(k, 0)) for k in keys) + " |")


if __name__ == "__main__":
    main()
