"""義務確認的量測（唯讀）：一個拋棄式庫、一個抽取版本的義務與罰則確認數、失敗型態與關鍵義務。

    uv run python tools/duty_report.py <dsn> <agent-version> [<agent-version> ...] [--json]

「關鍵義務」是被最多罰則條文以「違反」引用的條文單元（`current_violation` 的 `duty` 型）；
單元有已確認的義務或禁止陳述，該關鍵義務算確認。涵蓋率是 duty 要件標籤涵蓋「應」之後原文的比例，
與抽取端的確定性檢查（`extract._gaps`）同一個算法。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from itertools import groupby
from typing import Any

import psycopg

from envcite_ontology.verification.extract import _MODAL, _gaps

LAWS = ("O0040001", "O0040010")
KEYWORDS = {"遺漏": "遺漏", "添加": "添加", "命名": "命名", "種類": "種類"}
DUTY = "s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')"


def _rows(conn: Any, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in conn.execute(sql, args).fetchall()]


def duties(conn: Any, version: str) -> list[dict[str, Any]]:
    """此版本的每個義務／禁止陳述：狀態、引文、要件標籤、失敗理由。"""
    out = []
    for sid, state, law, path, content, span in _rows(
        conn,
        "SELECT s.id, a.confirm_state::text, r.local_id, p.path, v.content, s.span"
        " FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
        " JOIN annotation.derivation d ON d.id = a.derivation_id"
        " JOIN corpus.provision p ON p.id = s.provision_id"
        " JOIN corpus.legal_resource r ON r.id = p.resource_id"
        " JOIN corpus.provision_version v ON v.provision_id = s.provision_id"
        "  AND v.content_sha256 = s.content_sha256"
        f" WHERE {DUTY} AND d.producer_version = %s AND r.local_id = ANY(%s) ORDER BY s.id",
        version,
        list(LAWS),
    ):
        text = str(content)
        quote = text if span is None else text[span.lower : span.upper]
        labels = [
            str(label)
            for (label,) in _rows(
                conn,
                "SELECT k.name FROM annotation.statement_condition c"
                " JOIN vocab.concept k ON k.id = c.concept_id"
                " WHERE c.statement_id = %s AND c.condition_role = 'duty'",
                sid,
            )
        ]
        reasons: list[str] = []
        for method, ev in _rows(
            conn,
            "SELECT DISTINCT ON (method) method, evidence FROM annotation.verification"
            " WHERE assertion_id = %s AND result = 'fail' AND method <> 'policy'"
            " ORDER BY method, id DESC",
            sid,
        ):
            reasons.append(method)
            if method == "panel":
                reasons += [
                    str(i)
                    for v in ev.get("votes", [])
                    if v.get("faithful") is False
                    for i in (v.get("issues") or [])
                ]
        rounds = _rows(
            conn,
            "SELECT a.round FROM annotation.extraction_attempt a"
            " JOIN annotation.statement s ON s.provision_id = a.provision_id"
            "  AND s.content_sha256 = a.content_sha256"
            " JOIN annotation.assertion x ON x.id = s.id"
            " JOIN annotation.derivation d ON d.id = x.derivation_id"
            " WHERE s.id = %s AND a.producer_version = d.producer_version"
            "  AND a.attempted_at >= d.run_at ORDER BY a.attempted_at LIMIT 1"
            if _has_round(conn)
            else "SELECT 0 WHERE %s IS NOT NULL",
            sid,
        )
        modal = _MODAL.search(quote)
        after = quote[modal.end() :] if modal else ""
        covered = 1.0
        if after:
            covered = 1 - sum(len(g) for g in _gaps(after, labels)) / len(after)
        out.append(
            {
                "id": sid,
                "round": int(rounds[0][0]) if rounds else 0,
                "span": (span.lower, span.upper) if span is not None else None,
                "state": state,
                "law": law,
                "path": path,
                "quote": quote,
                "unit_has_modal": bool(_MODAL.search(text)),
                "quote_has_modal": modal is not None,
                "coverage": covered,
                "reasons": reasons,
            }
        )
    return out


def key_duties(conn: Any, version: str) -> list[dict[str, Any]]:
    """被引用最多的單元（每個被引條文：引用它的處罰條文數），與其中是否有已確認的義務。"""
    rows = _rows(
        conn,
        "SELECT c.id, r.local_id, c.path, count(DISTINCT vi.provision_id) AS n,"
        "  EXISTS (SELECT 1 FROM annotation.statement s"
        "   JOIN annotation.assertion a ON a.id = s.id"
        "   JOIN annotation.derivation d ON d.id = a.derivation_id"
        "   JOIN corpus.provision p ON p.id = s.provision_id"
        f"  WHERE {DUTY} AND a.confirm_state = 'confirmed' AND d.producer_version = %s"
        "    AND p.resource_id = c.resource_id"
        "    AND (p.path = c.path OR p.path LIKE c.path || '/%%' OR c.path LIKE p.path || '/%%'))"
        " FROM annotation.current_violation vi"
        " JOIN corpus.provision c ON c.id = vi.cited_provision_id"
        " JOIN corpus.legal_resource r ON r.id = c.resource_id"
        " WHERE vi.violation_kind = 'duty' AND r.local_id = ANY(%s)"
        " GROUP BY c.id, r.local_id, c.path, c.resource_id ORDER BY n DESC, c.path",
        version,
        list(LAWS),
    )
    return [{"law": r[1], "path": r[2], "penalties": r[3], "confirmed": r[4]} for r in rows]


def report(dsn: str, version: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as conn:
        ds = duties(conn, version)
        keys = key_duties(conn, version)
        pen = Counter(
            str(r[0])
            for r in _rows(
                conn,
                "SELECT a.confirm_state::text FROM annotation.statement s"
                " JOIN annotation.assertion a ON a.id = s.id"
                " JOIN annotation.derivation d ON d.id = a.derivation_id"
                " WHERE s.statement_kind = 'sanction' AND d.producer_version = %s",
                version,
            )
        )
        held = Counter(
            str(r[0])
            for r in _rows(
                conn,
                "SELECT state FROM annotation.held_penalty_state WHERE producer_version = %s",
                version,
            )
        )
        calls = (
            _rows(
                conn,
                "SELECT round, count(*) FROM annotation.extraction_attempt"
                " WHERE producer_version = %s GROUP BY round ORDER BY round",
                version,
            )
            if _has_round(conn)
            else []
        )
    state = Counter(d["state"] for d in ds)
    rejected = [d for d in ds if d["state"] == "rejected"]
    confirmed = [d for d in ds if d["state"] == "confirmed"]
    top = keys[:10]
    by_round = Counter(d["round"] for d in confirmed)
    units = {(d["law"], d["path"]) for d in ds}
    solved = {(d["law"], d["path"]) for d in confirmed}
    return {
        "version": version,
        "duty": dict(state),
        "confirmed_by_round": dict(sorted(by_round.items())),
        "duty_units": {"with_duty": len(units), "confirmed": len(solved)},
        "confirmed_spans": _spans(confirmed),
        "sanction": dict(pen),
        "held": dict(held),
        "attempts_by_round": {int(r[0]): int(r[1]) for r in calls},
        "rejected_causes": {
            "quote_without_modal": sum(not d["quote_has_modal"] for d in rejected),
            "unit_without_modal": sum(not d["unit_has_modal"] for d in rejected),
            **{
                f"panel_{k}": sum(any(w in r for r in d["reasons"]) for d in rejected)
                for k, w in KEYWORDS.items()
            },
            "failed_methods": dict(
                Counter(m for d in rejected for m in d["reasons"] if re.fullmatch(r"[a-z_]+", m))
            ),
        },
        "coverage_median": {
            "confirmed": _median([d["coverage"] for d in confirmed]),
            "rejected": _median([d["coverage"] for d in rejected]),
        },
        "key_duties": {
            "cited_units": len(keys),
            "confirmed": sum(k["confirmed"] for k in keys),
            "top10_confirmed": sum(k["confirmed"] for k in top),
            "top10": [
                f"{k['path']}({k['penalties']}){'+' if k['confirmed'] else '-'}" for k in top
            ],
        },
    }


def _unit_key(d: dict[str, Any]) -> tuple[str, str]:
    return d["law"], d["path"]


def _spans(ds: list[dict[str, Any]]) -> int:
    """已確認義務涵蓋的互不重疊引文段數：同一段的幾種寫法只算一段。"""
    n = 0
    for _, group in groupby(sorted(ds, key=_unit_key), key=_unit_key):
        end = -1
        for lo, hi in sorted(d["span"] or (0, 10**9) for d in group):
            if lo >= end:
                n += 1
            end = max(end, hi)
    return n


def _has_round(conn: Any) -> bool:
    return bool(
        _rows(
            conn,
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'annotation'"
            " AND table_name = 'extraction_attempt' AND column_name = 'round'",
        )
    )


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dsn")
    parser.add_argument("versions", nargs="+")
    args = parser.parse_args()
    for v in args.versions:
        json.dump(report(args.dsn, v), sys.stdout, ensure_ascii=False, indent=1)
        print()


if __name__ == "__main__":
    main()
