"""義務重抽的成本、重複確認的浪費，與主體「未明示」在罰則鏈上的計數（唯讀）。

    uv run python tools/duty_cost.py cost --dsn 'postgresql://…/{db}' \\
        --run pre-1=pre_1:run-pre-1.log --run post-1=post_1:run-post-1.log --out cost.json
    uv run python tools/duty_cost.py unstated --dsn 'postgresql://…/{db}' \\
        --run post-1=post_1 --run post-2=post_2 --out unstated.json

成本：每輪的抽取嘗試數、陳述數、確認數、驗證紀錄列數（每列是一個閘門的一次結果，模型閘門每列至少一次
呼叫），加上管線日誌回報的抽取呼叫數與詞元、輪數與耗時。

浪費：第 1 輪起抽出、引文與「更早的輪已確認」的陳述重疊的陳述。重抽對仍有未決義務的單元整個再抽，
所以已確認的引文區間會被再抽、再驗證一次；這裡數它們有多少、花了多少驗證紀錄、又被確認了多少次。

未明示：已確認的義務有多少主體記成「未明示」，其中有多少被已確認的罰則處罰（罰則鏈上的義務），
逐項列出單元、引文與罰則；另數罰則自己的主體槽位（推論只讀罰則的主體槽位）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from duty_effect import context_of

DUTY = "s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')"

_LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_LOG_ROUNDS = re.compile(r"(\d+) 個確認、(\d+) 個駁回，(\d+) 輪")
_LOG_CALLS = re.compile(
    r"抽取計數：呼叫 (\d+)，修復 (\d+)，語法失敗 (\d+)，形狀問題 (\d+)；詞元 (\{[^}]*\}|None)"
)


def parse_log(text: str) -> dict[str, Any]:
    """管線日誌的摘要：抽取呼叫數、修復數、詞元、輪數與起訖時間。日誌沒有那一行就是 None。"""
    out: dict[str, Any] = {
        "extract_calls": None,
        "repairs": None,
        "tokens": None,
        "rounds": None,
        "seconds": None,
    }
    times = [m.group(1) for line in text.splitlines() if (m := _LOG_TIME.match(line))]
    if len(times) >= 2:
        fmt = "%Y-%m-%d %H:%M:%S"
        out["seconds"] = int(
            (datetime.strptime(times[-1], fmt) - datetime.strptime(times[0], fmt)).total_seconds()
        )
    if found := _LOG_CALLS.search(text):
        out["extract_calls"], out["repairs"] = int(found[1]), int(found[2])
        out["tokens"] = None if found[5] == "None" else json.loads(found[5].replace("'", '"'))
    if found := _LOG_ROUNDS.search(text):
        out["rounds"] = int(found[3])
    return out


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def redundant(statements: Sequence[Mapping[str, Any]]) -> list[int]:
    """第 1 輪起抽出、引文與更早的輪已確認的陳述重疊（同一單元）的陳述編號。"""
    confirmed_before: dict[int, list[tuple[int, tuple[int, int]]]] = defaultdict(list)
    for s in statements:
        if s["confirmed"]:
            confirmed_before[s["provision_id"]].append((s["round"], tuple(s["span"])))  # type: ignore[arg-type]
    out = []
    for s in statements:
        if s["round"] < 1:
            continue
        if any(
            r < s["round"] and overlaps(tuple(s["span"]), span)  # type: ignore[arg-type]
            for r, span in confirmed_before[s["provision_id"]]
        ):
            out.append(s["id"])
    return out


_ROUND = (
    "(SELECT x.round FROM annotation.extraction_attempt x"
    "  WHERE x.provision_id = s.provision_id AND x.content_sha256 = s.content_sha256"
    "    AND x.producer_version = d.producer_version AND x.attempted_at >= d.run_at"
    "  ORDER BY x.attempted_at LIMIT 1)"
)


def duty_rows(conn: Any, version: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.id, s.provision_id, coalesce(lower(s.span), 0),"
        "       coalesce(upper(s.span), length(v.content)), a.confirm_state::text,"
        f"       {_ROUND},"
        "       (SELECT count(*) FROM annotation.verification w WHERE w.assertion_id = s.id)"
        "  FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
        "  JOIN annotation.derivation d ON d.id = a.derivation_id"
        "  JOIN corpus.provision_version v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256"
        f" WHERE {DUTY} AND d.producer_version = %s ORDER BY s.id",
        (version,),
    ).fetchall()
    return [
        {
            "id": int(sid),
            "provision_id": int(pid),
            "span": [int(lo), int(hi)],
            "confirmed": state == "confirmed",
            "state": str(state),
            "round": int(rnd or 0),
            "verifications": int(ver),
        }
        for sid, pid, lo, hi, state, rnd, ver in rows
    ]


def cost_of(conn: Any, version: str) -> dict[str, Any]:
    """一個執行的成本與浪費。"""
    attempts = {
        int(r): int(n)
        for r, n in conn.execute(
            "SELECT round, count(*) FROM annotation.extraction_attempt"
            " WHERE producer_version = %s GROUP BY round ORDER BY round",
            (version,),
        )
    }
    rows = duty_rows(conn, version)
    dup = set(redundant(rows))
    by_round: dict[int, dict[str, int]] = defaultdict(
        lambda: {"statements": 0, "confirmed": 0, "verifications": 0, "redundant": 0}
    )
    for r in rows:
        b = by_round[r["round"]]
        b["statements"] += 1
        b["confirmed"] += r["confirmed"]
        b["verifications"] += r["verifications"]
        b["redundant"] += r["id"] in dup
    later = [r for r in rows if r["round"] >= 1]
    spent = sum(r["verifications"] for r in later)
    wasted = [r for r in rows if r["id"] in dup]
    return {
        "attempts_by_round": attempts,
        "attempts_total": sum(attempts.values()),
        "by_round": {str(k): v for k, v in sorted(by_round.items())},
        "duty_statements": len(rows),
        "confirmed": sum(r["confirmed"] for r in rows),
        "verification_rows": sum(r["verifications"] for r in rows),
        "reextract_statements": len(later),
        "reextract_verification_rows": spent,
        "redundant_statements": len(wasted),
        "redundant_confirmed": sum(r["confirmed"] for r in wasted),
        "redundant_verification_rows": sum(r["verifications"] for r in wasted),
        "redundant_share_of_reextract_verifications": (
            round(sum(r["verifications"] for r in wasted) / spent, 3) if spent else None
        ),
    }


# 主體未明示


def unstated_of(conn: Any, version: str) -> dict[str, Any]:
    """已確認義務的「未明示」主體、其罰則鏈，與罰則自己的主體槽位。"""
    has = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'annotation'"
        " AND table_name = 'statement_slot' AND column_name = 'unstated'"
    ).fetchone()
    flag = "sl.unstated" if has else "false"
    duties = conn.execute(
        "SELECT s.id, s.provision_id, r.local_id, p.path, v.content, coalesce(lower(s.span), 0),"
        "       coalesce(upper(s.span), length(v.content)), s.norm_modality::text,"
        f"       bool_or({flag}) FILTER (WHERE sl.slot_role = 'subject'),"
        f"       {_ROUND}"
        "  FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
        "  JOIN annotation.derivation d ON d.id = a.derivation_id"
        "  JOIN corpus.provision p ON p.id = s.provision_id"
        "  JOIN corpus.legal_resource r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256"
        "  LEFT JOIN annotation.statement_slot sl ON sl.statement_id = s.id"
        f" WHERE {DUTY} AND a.confirm_state = 'confirmed' AND d.producer_version = %s"
        " GROUP BY s.id, r.local_id, p.path, v.content, d.producer_version, d.run_at"
        " ORDER BY s.id",
        (version,),
    ).fetchall()
    penalties: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for pid, punished, effect, lo, hi, per_occasion, text, unst, basis in conn.execute(
        "SELECT n.statement_id, n.punished_statement_id, n.legal_effect::text, n.min_amount,"
        "       n.max_amount, n.per_occasion, pv.content,"
        f"       bool_or({flag}) FILTER (WHERE sl.slot_role = 'subject'),"
        "       array_agg(sl.liability_basis::text) FILTER (WHERE sl.liability_basis IS NOT NULL)"
        "  FROM annotation.sanction n JOIN annotation.assertion pa ON pa.id = n.statement_id"
        "  JOIN annotation.statement ps ON ps.id = n.statement_id"
        "  JOIN corpus.provision_version pv ON pv.provision_id = ps.provision_id"
        "   AND pv.content_sha256 = ps.content_sha256"
        "  LEFT JOIN annotation.statement_slot sl ON sl.statement_id = n.statement_id"
        " WHERE pa.confirm_state = 'confirmed'"
        " GROUP BY n.statement_id, n.punished_statement_id, n.legal_effect, n.min_amount,"
        "          n.max_amount, n.per_occasion, pv.content",
    ):
        penalties[int(punished)].append(
            {
                "penalty_id": int(pid),
                "effect": str(effect),
                "min": None if lo is None else str(lo),
                "max": None if hi is None else str(hi),
                "per_occasion": bool(per_occasion),
                "text": str(text)[:80],
                "subject_unstated": bool(unst),
                "subject_bases": sorted(set(basis or [])),
            }
        )
    items = []
    for sid, pid, law, path, content, lo, hi, modality, unst, rnd in duties:
        if not unst:
            continue
        items.append(
            {
                "id": int(sid),
                "unit": f"{law} {path}",
                "round": int(rnd or 0),
                "deontic": str(modality),
                "quote": str(content)[lo:hi],
                "text": str(content),
                "context": context_of(conn, int(pid)),
                "penalties": penalties.get(int(sid), []),
            }
        )
    sanctions = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM annotation.statement_slot sl"
        "         WHERE sl.statement_id = n.statement_id AND sl.slot_role = 'subject'"
        "           AND sl.liability_basis IS NOT NULL)),"
        f"       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM annotation.statement_slot sl"
        f"         WHERE sl.statement_id = n.statement_id AND sl.slot_role = 'subject' AND {flag}))"
        "  FROM annotation.sanction n JOIN annotation.assertion pa ON pa.id = n.statement_id"
        " WHERE pa.confirm_state = 'confirmed'"
    ).fetchone()
    return {
        "confirmed_duties": len(duties),
        "unstated_duties": len(items),
        "unstated_units": len({i["unit"] for i in items}),
        "unstated_on_penalty_chain": sum(1 for i in items if i["penalties"]),
        "unstated_chain_units": len({i["unit"] for i in items if i["penalties"]}),
        "confirmed_penalties": int(sanctions[0]),
        "penalties_with_liability_basis": int(sanctions[1]),
        "penalties_with_unstated_subject": int(sanctions[2]),
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("cost", "unstated"):
        p = sub.add_parser(name)
        p.add_argument("--dsn", required=True, help="含 {db} 的連線字串")
        p.add_argument("--run", action="append", required=True, help="版本=庫名[:日誌]，可重複")
        p.add_argument("--out", required=True)
    args = parser.parse_args()
    result: dict[str, Any] = {}
    for spec in args.run:
        version, rest = spec.split("=", 1)
        db, _, log = rest.partition(":")
        with psycopg.connect(args.dsn.format(db=db)) as conn:
            if args.cmd == "cost":
                result[version] = cost_of(conn, version)
                if log:
                    result[version]["log"] = parse_log(Path(log).read_text(encoding="utf-8"))
            else:
                result[version] = unstated_of(conn, version)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {args.out}")


if __name__ == "__main__":
    main()
