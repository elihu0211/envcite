"""義務確認的抽查樣本（唯讀）：從一個抽取版本已確認的義務與禁止陳述抽樣。

印出單元原文、引文、槽位與要件。

    uv run python tools/duty_sample.py <dsn> <agent-version> --seed 1 --n 10 --key 2
    uv run python tools/duty_sample.py <dsn> <agent-version> --seed 2 --n 8 --key 1 \
        --min-round 1 --exclude 9542,9720

逐筆對照條文原文判斷主體、義務內容與要件是否忠於原文；種子與參數固定，樣本可重現。
`--key K` 至少抽 K 筆在關鍵義務單元上的（被處罰條文以「違反」引用的單元，同 `duty_report.py`），
`--min-round` 只抽此輪以後（重抽）確認的，`--exclude` 排除已看過的陳述編號。
"""

from __future__ import annotations

import argparse
import random
from typing import Any

import psycopg

DUTY = "s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')"


def key_units(conn: Any) -> set[tuple[str, str]]:
    rows = conn.execute(
        "SELECT r.local_id, c.path FROM annotation.current_violation vi"
        " JOIN corpus.provision c ON c.id = vi.cited_provision_id"
        " JOIN corpus.legal_resource r ON r.id = c.resource_id"
        " WHERE vi.violation_kind = 'duty'"
    ).fetchall()
    return {(str(law), str(path)) for law, path in rows}


def confirmed(conn: Any, version: str) -> list[tuple[Any, ...]]:
    """已確認的義務與禁止陳述；`round` 是它所屬抽取嘗試的輪號（沒有輪號欄的舊庫一律 0）。"""
    has_round = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'annotation'"
        " AND table_name = 'extraction_attempt' AND column_name = 'round'"
    ).fetchone()
    rnd = (
        "(SELECT x.round FROM annotation.extraction_attempt x"
        "  WHERE x.provision_id = s.provision_id AND x.content_sha256 = s.content_sha256"
        "    AND x.producer_version = d.producer_version AND x.attempted_at >= d.run_at"
        "  ORDER BY x.attempted_at LIMIT 1)"
        if has_round
        else "0"
    )
    return list(
        conn.execute(
            "SELECT s.id, r.local_id, p.path, v.content, s.span, s.norm_modality::text,"
            f" {rnd}"
            " FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
            " JOIN annotation.derivation d ON d.id = a.derivation_id"
            " JOIN corpus.provision p ON p.id = s.provision_id"
            " JOIN corpus.legal_resource r ON r.id = p.resource_id"
            " JOIN corpus.provision_version v ON v.provision_id = s.provision_id"
            "  AND v.content_sha256 = s.content_sha256"
            f" WHERE {DUTY} AND a.confirm_state = 'confirmed' AND d.producer_version = %s"
            " ORDER BY s.id",
            (version,),
        ).fetchall()
    )


def detail(conn: Any, sid: int) -> tuple[list[str], list[str]]:
    slots = [
        f"{role}={name}" + (f"[{basis}]" if basis else "")
        for role, name, basis in conn.execute(
            "SELECT sl.slot_role::text, coalesce(k.name, sl.literal), sl.liability_basis::text"
            " FROM annotation.statement_slot sl LEFT JOIN vocab.concept k ON k.id = sl.concept_id"
            " WHERE sl.statement_id = %s ORDER BY 1, sl.alternative",
            (sid,),
        )
    ]
    conds = [
        f"{role}/{disjunct}:{name}{' NOT' if negated else ''}"
        for role, disjunct, name, negated in conn.execute(
            "SELECT c.condition_role::text, c.disjunct_no, k.name, c.negated"
            " FROM annotation.statement_condition c JOIN vocab.concept k ON k.id = c.concept_id"
            " WHERE c.statement_id = %s ORDER BY c.condition_role, c.disjunct_no, c.id",
            (sid,),
        )
    ]
    return slots, conds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dsn")
    parser.add_argument("version")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n", type=int, default=10, help="總抽樣數（關鍵義務算在內）")
    parser.add_argument("--key", type=int, default=2, help="關鍵義務至少抽幾筆")
    parser.add_argument("--min-round", type=int, default=None, help="只抽此輪（含）以後確認的")
    parser.add_argument("--exclude", default="", help="排除的陳述編號，逗號分隔")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    excluded = {int(x) for x in args.exclude.split(",") if x}
    with psycopg.connect(args.dsn) as conn:
        keys = key_units(conn)
        rows = [r for r in confirmed(conn, args.version) if r[0] not in excluded]
        if args.min_round is not None:
            rows = [r for r in rows if int(r[6] or 0) >= args.min_round]
        on_key = [r for r in rows if (str(r[1]), str(r[2])) in keys]
        others = [r for r in rows if (str(r[1]), str(r[2])) not in keys]
        picked = rng.sample(on_key, min(args.key, len(on_key)))
        picked += rng.sample(others, min(max(args.n - len(picked), 0), len(others)))
        for sid, law, path, content, span, modality, rnd in picked:
            text = str(content)
            quote = text if span is None else text[span.lower : span.upper]
            slots, conds = detail(conn, int(sid))
            flag = "關鍵" if (str(law), str(path)) in keys else "一般"
            print(f"#{sid} {law} {path} 輪={int(rnd or 0)} {flag} {modality}")
            print(f"  單元：{text}")
            print(f"  引文：{quote}")
            print(f"  槽位：{'; '.join(slots)}")
            print(f"  要件：{'; '.join(conds)}")
            print()


if __name__ == "__main__":
    main()
