"""Environmental-regulation fields (migrations 0033–0038) measured on real provisions.

    uv run python tools/env_realtest.py prepare realtest 55491            # slice → throwaway DB
    uv run python tools/env_realtest.py pipeline <dsn> --models models.toml --agent-version V \
        --log eval/<name>.log                                            # the repo pipeline, as is
    uv run python tools/env_realtest.py report <dsn> --log eval/<name>.log --out eval/<name>.json

The slice and the expectations are `eval/env-realtest-2026-10-28.expected.json`, written before any
run and not changed after. `prepare` rebuilds the listed articles from the development database
(read-only) into a throwaway postgres container through the real importer, the same way
`holdout.py` does. `pipeline` runs `envcite-pipeline` on every law of the slice in one call, so the
link step sees the penalties of all of them. `report` dumps what was written (statements, slots,
conditions, sanctions, triggers, verification records, dropped outputs from the log), scores it
field by field against the expectations, and runs the reasoning scenarios. The key comes from
`models.toml` as it declares; this script never reads or writes a key itself.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from holdout import DEV_DSN, _articles, start_database

from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.migrate import apply_all
from envcite_ontology.reasoning import facts
from envcite_ontology.reasoning.engine import evaluate
from tests.sample import stored_snapshot

EXPECTED = Path(__file__).resolve().parents[1] / "eval" / "env-realtest-2026-10-28.expected.json"
FEED = {"法律": "moj_law", "命令": "moj_order"}
FIELDS = (
    "kind",
    "deontic",
    "effect",
    "repeating",
    "triggers",
    "liability",
    "judgement",
    "permit",
    "averaging",
    "method",
    "punishes",
)


def _expected() -> dict[str, Any]:
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


def _law(
    c: psycopg.Connection[Any], pcode: str, level: str, wanted: Sequence[str]
) -> dict[str, Any]:
    rid, title = c.execute(
        "SELECT r.id, e.title FROM corpus.legal_resource AS r"
        "  JOIN corpus.legal_expression AS e ON e.resource_id = r.id"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s ORDER BY e.version_date DESC LIMIT 1",
        (pcode,),
    ).fetchone()
    events = [
        str(r[0])
        for r in c.execute(
            "SELECT source_text FROM corpus.lifecycle_event"
            " WHERE resource_id = %s AND source_text IS NOT NULL ORDER BY event_date, id",
            (rid,),
        )
    ]
    arts = _articles(c, int(rid))
    missing = [a for a in wanted if a not in arts]
    if missing:
        sys.exit(f"{pcode} has no {missing} in the development database")
    return {
        "LawLevel": level,
        "LawName": str(title),
        "LawURL": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}",
        "LawModifiedDate": "",
        "LawEffectiveDate": "",
        "LawEffectiveNote": "",
        "LawAbandonNote": "",
        "LawHistories": "\r\n".join(events),
        "LawArticles": [
            {"ArticleType": "A", "ArticleNo": f"第 {a[4:]} 條", "ArticleContent": arts[a]}
            for a in wanted
        ],
    }


def prepare(name: str, port: int) -> str:
    spec = _expected()["slice"]
    with psycopg.connect(DEV_DSN, connect_timeout=5) as c:
        c.read_only = True
        laws = [(s["level"], _law(c, s["pcode"], s["level"], s["articles"])) for s in spec]
    dsn = start_database(name, port)
    with psycopg.connect(dsn) as conn:
        apply_all(conn)
        with tempfile.TemporaryDirectory() as t:
            for level, feed in FEED.items():
                stored_snapshot(conn, Path(t), [law for lv, law in laws if lv == level], feed=feed)
            importer.run(conn, Path(t), Moj())
        conn.commit()
        units = conn.execute(
            "SELECT count(*) FROM corpus.provision_version WHERE superseded_at IS NULL"
            " AND content IS NOT NULL"
        ).fetchone()
    print(f"{dsn}: {units[0] if units else 0} units")
    return dsn


def pipeline(dsn: str, models: str, agent_version: str, log: Path) -> int:
    laws = [a for s in _expected()["slice"] for a in ("--law", s["pcode"])]
    with log.open("w", encoding="utf-8") as out:
        code = subprocess.run(
            [
                "uv",
                "run",
                "envcite-pipeline",
                *laws,
                "--models",
                models,
                "--agent-version",
                agent_version,
            ],
            env={**__import__("os").environ, "ENVCITE_ONTOLOGY_DSN": dsn},
            stdout=out,
            stderr=subprocess.STDOUT,
        ).returncode
        out.write(f"exit={code}\n")
    return code


_STATEMENTS = """
SELECT s.id, r.local_id, p.path, v.content, lower(s.span), upper(s.span), s.statement_kind::text,
       s.norm_modality::text, a.confirm_state::text, d.producer, d.producer_version
  FROM annotation.statement AS s
  JOIN annotation.assertion AS a ON a.id = s.id
  JOIN annotation.derivation AS d ON d.id = a.derivation_id
  JOIN corpus.provision AS p ON p.id = s.provision_id
  JOIN corpus.legal_resource AS r ON r.id = p.resource_id
  JOIN corpus.provision_version AS v ON v.provision_id = p.id
                                    AND v.content_sha256 = s.content_sha256
                                    AND v.superseded_at IS NULL
 ORDER BY s.id
"""

_CONDITIONS = """
SELECT c.id, c.condition_role::text, c.disjunct_no, k.code, k.name, c.comparator::text, c.threshold,
       c.measure_unit, c.negated, c.needs_assessment, vk.code, pk.code, pk.name, dp.path,
       mk.code, mk.name, ak.code, ak.name
  FROM annotation.statement_condition AS c
  JOIN vocab.concept AS k ON k.id = c.concept_id
  LEFT JOIN vocab.concept AS vk ON vk.id = c.value_concept_id
  LEFT JOIN vocab.concept AS pk ON pk.id = c.delegate_permit_concept_id
  LEFT JOIN vocab.concept AS mk ON mk.id = c.measurement_method_concept_id
  LEFT JOIN vocab.concept AS ak ON ak.id = c.averaging_period_concept_id
  LEFT JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
 WHERE c.statement_id = %s ORDER BY c.id
"""


def _num(x: Any) -> Any:
    return (
        None
        if x is None
        else (int(x) if isinstance(x, Decimal) and x == x.to_integral() else float(x))
    )


def dump(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    where = {}
    for sid, law, path, text, lo, hi, kind, deontic, state, producer, version in conn.execute(
        _STATEMENTS
    ).fetchall():
        quote = text if lo is None else text[lo:hi]
        where[sid] = (law, path)
        slots = [
            {"role": r, "alternative": alt, "literal": lit, "concept": code, "liability": lb}
            for r, alt, lit, code, lb in conn.execute(
                "SELECT sl.slot_role::text, sl.alternative, sl.literal, k.code,"
                " sl.liability_basis::text FROM annotation.statement_slot AS sl"
                " LEFT JOIN vocab.concept AS k ON k.id = sl.concept_id"
                " WHERE sl.statement_id = %s ORDER BY sl.slot_role, sl.alternative",
                (sid,),
            )
        ]
        conditions = [
            {
                "id": cid,
                "role": role,
                "clause": clause,
                "concept": code,
                "label": name,
                "comparator": comp,
                "threshold": _num(thr),
                "unit": unit,
                "negated": neg,
                "judgement": jud,
                "value_concept": vk,
                "permit": pk,
                "permit_label": pn,
                "delegate": dpath,
                "method": mk,
                "method_label": mn,
                "averaging": ak,
                "averaging_label": an,
            }
            for (
                cid,
                role,
                clause,
                code,
                name,
                comp,
                thr,
                unit,
                neg,
                jud,
                vk,
                pk,
                pn,
                dpath,
                mk,
                mn,
                ak,
                an,
            ) in conn.execute(_CONDITIONS, (sid,))
        ]
        row = conn.execute(
            "SELECT legal_effect::text, min_amount, max_amount, per_occasion, discretionary,"
            " amount_text, punished_statement_id FROM annotation.sanction WHERE statement_id = %s",
            (sid,),
        ).fetchone()
        sanction = None
        if row is not None:
            effect, lo_, hi_, rep, disc, amount, punished = row
            sanction = {
                "effect": effect,
                "min": _num(lo_),
                "max": _num(hi_),
                "repeating": rep,
                "discretionary": disc,
                "amount_text": amount,
                "punished_id": punished,
            }
        triggers = [
            {"kind": k, "statement_id": t}
            for t, k in conn.execute(
                "SELECT trigger_statement_id, trigger_kind::text FROM annotation.sanction_trigger"
                " WHERE statement_id = %s ORDER BY trigger_statement_id",
                (sid,),
            )
        ]
        verifications = [
            {
                "method": m,
                "verifier": who,
                "result": res,
                "evidence": json.dumps(ev, ensure_ascii=False)[:400],
            }
            for m, who, res, ev in conn.execute(
                "SELECT method, verifier, result::text, evidence FROM annotation.verification"
                " WHERE assertion_id = %s ORDER BY id",
                (sid,),
            )
        ]
        out.append(
            {
                "id": sid,
                "law": law,
                "path": path,
                "quote": quote,
                "whole_unit": lo is None,
                "kind": kind,
                "deontic": deontic,
                "state": state,
                "producer": producer,
                "version": version,
                "slots": slots,
                "conditions": conditions,
                "sanction": sanction,
                "triggers": triggers,
                "verifications": verifications,
            }
        )
    by_id = {s["id"]: s for s in out}
    for s in out:
        if s["sanction"] and s["sanction"]["punished_id"] in by_id:
            p = by_id[s["sanction"]["punished_id"]]
            s["sanction"]["punished"] = f"{p['law']} {p['path']}"
        for t in s["triggers"]:
            q = by_id.get(t["statement_id"])
            if q is not None:
                t["target"] = f"{q['law']} {q['path']}"
                t["target_effect"] = (q["sanction"] or {}).get("effect")
    return out


def _log_lines(log: Path) -> dict[str, list[str]]:
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return {
        "dropped": re.findall(r"WARNING (丟掉.*)", text),
        "summary": re.findall(r"INFO ((?:單元|抽取計數|模型|法規).*)", text),
        "errors": re.findall(r"(Traceback.*|.*Error:.*|exit=\d+)", text),
    }


def _actual_field(s: Mapping[str, Any], field: str, by_id: Mapping[int, Any]) -> Any:
    sanction = s.get("sanction") or {}
    conds = s.get("conditions") or []
    if field in ("kind", "deontic"):
        return s.get(field)
    if field in ("effect", "repeating"):
        return sanction.get(field)
    if field == "liability":
        values = {x["liability"] for x in s["slots"] if x["role"] == "subject" and x["liability"]}
        return sorted(values)[0] if len(values) == 1 else (sorted(values) or None)
    if field == "judgement":
        return any(c["judgement"] for c in conds)
    if field == "permit":
        return next((c["permit_label"] or c["permit"] for c in conds if c["permit"]), None)
    if field in ("averaging", "method"):
        return next((c[f"{field}_label"] or c[field] for c in conds if c[field]), None)
    if field == "punishes":
        p = by_id.get(sanction.get("punished_id"))
        return None if p is None else p["path"]
    if field == "triggers":
        return [(t["kind"], t["statement_id"]) for t in s["triggers"]]
    raise KeyError(field)


def score(statements: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    expected = _expected()["expected"]
    by_id = {s["id"]: s for s in statements}
    used: set[int] = set()
    match: dict[str, Any] = {}
    for e in expected:
        here = [
            s
            for s in statements
            if s["law"] == e["law"]
            and s["path"] == e["path"]
            and s["id"] not in used
            and e["anchor"] in s["quote"]
        ]
        best = min(here, key=lambda s: (len(s["quote"]), s["id"]), default=None)
        match[e["id"]] = best
        if best is not None:
            used.add(best["id"])
    matched_ids = {e_id: (s["id"] if s else None) for e_id, s in match.items()}
    tally = {f: {"hit": 0, "wrong": 0, "miss": 0, "false_positive": 0, "n": 0} for f in FIELDS}
    rows: list[dict[str, Any]] = []
    for e in expected:
        s = match[e["id"]]
        row: dict[str, Any] = {
            "expected": e["id"],
            "statement": s["id"] if s else None,
            "state": s["state"] if s else None,
            "fields": {},
        }
        for f in FIELDS:
            want = e.get(f)
            got = None if s is None else _actual_field(s, f, by_id)
            if f == "triggers":
                if s is not None:
                    got_set = set()
                    for kind, target in got or []:
                        t = by_id.get(target)
                        if t is None:
                            continue
                        other = next((k for k, v in matched_ids.items() if v == target), None)
                        cross = t["law"] != s["law"] and (t["sanction"] or {}).get("effect") in (
                            "fine",
                            "restriction",
                        )
                        got_set.add(
                            (
                                kind,
                                "cross_law"
                                if cross
                                and kind == "sanction_imposed"
                                and ["sanction_imposed", "cross_law"] in (want or [])
                                else other or f"#{target}",
                            )
                        )
                    got = sorted(got_set)
                want_set = sorted(tuple(x) for x in want) if want else want
                if not want:
                    if got:
                        tally[f]["false_positive"] += 1
                        row["fields"][f] = ("false_positive", want, got)
                    continue
                verdict = "miss" if not got else ("hit" if got == want_set else "wrong")
            elif want is None or want is False or (f == "liability" and want == "act_or_null"):
                kinds = e["kind"] if isinstance(e["kind"], list) else [e["kind"]]
                if f == "deontic" and "prescriptive" in kinds:
                    continue  # 種類可為規範性而未指定方向：方向不計分
                written = got not in (None, "act") if f == "liability" else got not in (None, False)
                if s is not None and written:
                    tally[f]["false_positive"] += 1
                    row["fields"][f] = ("false_positive", want, got)
                continue
            elif got in (None, False, []):
                verdict = "miss"
            elif f in ("permit", "averaging", "method"):
                verdict = "hit" if want in str(got) else "wrong"
            elif f == "kind":
                verdict = "hit" if got in (want if isinstance(want, list) else [want]) else "wrong"
            elif f == "punishes":
                verdict = "hit" if got in want else "wrong"
            else:
                verdict = "hit" if got == want else "wrong"
            tally[f]["n"] += 1
            tally[f][verdict] += 1
            row["fields"][f] = (verdict, want, got)
        rows.append(row)
    extra = [
        {
            "id": s["id"],
            "law": s["law"],
            "path": s["path"],
            "kind": s["kind"],
            "effect": (s["sanction"] or {}).get("effect"),
            "state": s["state"],
        }
        for s in statements
        if s["id"] not in used
    ]
    return {
        "tally": tally,
        "rows": rows,
        "extra": extra,
        "matched": matched_ids,
        "expected_count": len(expected),
        "matched_count": len(used),
    }


def _value(c: Mapping[str, Any], hold: bool) -> float:
    """A fact value that makes the condition hold (or fail), given its comparator and threshold."""
    t = float(c["threshold"])
    holds = {
        "eq": (t, t + 1),
        "ne": (t + 1, t),
        "le": (t, t + 1),
        "lt": (t - 1, t),
        "ge": (t, t - 1),
        "gt": (t + 1, t),
    }[c["comparator"]]
    want = hold != bool(c["negated"])
    return holds[0] if want else holds[1]


def _case(
    conditions: Sequence[Mapping[str, Any]], role_hold: Mapping[str, bool]
) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in conditions:
        if c["role"] not in role_hold or c["threshold"] is None or c["judgement"]:
            continue
        out[c["concept"]] = _value(c, role_hold[c["role"]])
    return out


def _result(r: Any) -> dict[str, Any]:
    return {
        "verdicts": {str(k): v for k, v in r.verdicts.items()},
        "penalties": [
            {
                "statement": p.statement_id,
                "punishes": p.punishes_id,
                "effect": p.effect,
                "conditional": p.conditional,
                "repeating": p.repeating,
                "discretionary": p.discretionary,
                "triggered_by": list(p.triggered_by),
            }
            for p in r.penalties
        ],
        "missing": list(r.missing),
        "needs_judgement": list(r.needs_judgement),
        "problems": list(r.problems),
        "missing_permits": list(r.missing_permits),
        "unpunished": list(r.general.unpunished),
    }


def reason(
    conn: psycopg.Connection[Any],
    statements: Sequence[Mapping[str, Any]],
    matched: Mapping[str, int | None],
) -> list[dict[str, Any]]:
    by_id = {s["id"]: s for s in statements}
    on = date.today()
    runs: list[dict[str, Any]] = []
    again = by_id.get(matched.get("w40a-again") or -1)
    linked = [
        by_id[sid]
        for e in ("w40a-fine", "w40a-order", "w40a-again", "w40a-halt")
        if (sid := matched.get(e)) is not None and by_id[sid]["sanction"]
    ]
    duty = next((by_id[p] for s in linked if (p := s["sanction"]["punished_id"]) in by_id), None)
    if duty is None:
        for name in ("lapse-unknown", "lapse-true"):
            runs.append({"scenario": name, "skipped": "no penalty of art_40/para_1 is linked"})
    else:
        # 違反義務是義務內容不成立；違反禁止是被禁止的內容成立
        base = _case(duty["conditions"], {"scope": True, "duty": duty["deontic"] == "prohibition"})
        lapsed = _case((again or {}).get("conditions", []), {"scope": True})
        for name, case in (
            ("lapse-unknown", base),
            ("lapse-true", base | lapsed | {"ap_intent": 1.0}),
        ):
            for unsettled in (False, True):
                r = evaluate(conn, on, case, include_unsettled=unsettled)
                runs.append(
                    {
                        "scenario": name,
                        "include_unsettled": unsettled,
                        "case": case,
                        "result": _result(r),
                    }
                )
    limit = by_id.get(matched.get("d5a") or -1)
    lims = [c for c in (limit or {}).get("conditions", []) if c["role"] == "limit"]
    delegating = [c for s in statements for c in s["conditions"] if c["delegate"] == "art_5/para_1"]
    if not lims or not delegating:
        runs.append(
            {
                "scenario": "limit-measurement",
                "skipped": f"limit conditions on art_5/para_1: {len(lims)}; "
                f"conditions delegating to it: {len(delegating)}",
            }
        )
    else:
        c = delegating[0]
        over = {c["concept"]: float(lims[0]["threshold"]) * 2}
        for label, m in (
            ("no method given", None),
            ("different method", facts.Measurement(method="__other__", averaging="__other__")),
        ):
            for unsettled in (False, True):
                r = evaluate(
                    conn,
                    on,
                    over,
                    include_unsettled=unsettled,
                    measurements=None if m is None else {c["concept"]: [m]},
                )
                runs.append(
                    {
                        "scenario": "limit-measurement",
                        "variant": label,
                        "include_unsettled": unsettled,
                        "case": over,
                        "result": _result(r),
                    }
                )
    return runs


def report(dsn: str, log: Path, out: Path) -> None:
    with psycopg.connect(dsn) as conn:
        statements = dump(conn)
        scored = score(statements)
        runs = reason(conn, statements, scored["matched"])
        states = dict(
            conn.execute(
                "SELECT confirm_state::text, count(*) FROM annotation.assertion"
                " WHERE assertion_kind = 'statement' GROUP BY 1"
            ).fetchall()
        )
    doc = {
        "log": _log_lines(log),
        "states": states,
        "score": scored,
        "reasoning": runs,
        "statements": statements,
    }
    out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8"
    )
    print(
        f"statements {len(statements)} {states}; matched {scored['matched_count']}/"
        f"{scored['expected_count']}; extra {len(scored['extra'])}"
    )
    print("| 欄位 | n | 命中 | 錯誤 | 遺漏 | 誤報 |")
    for f, t in scored["tally"].items():
        print(
            f"| {f} | {t['n']} | {t['hit']} | {t['wrong']} | {t['miss']} | {t['false_positive']} |"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("name")
    p.add_argument("port", type=int)
    p = sub.add_parser("pipeline")
    p.add_argument("dsn")
    p.add_argument("--models", required=True)
    p.add_argument("--agent-version", required=True)
    p.add_argument("--log", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("dsn")
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args.name, args.port)
    elif args.cmd == "pipeline":
        sys.exit(pipeline(args.dsn, args.models, args.agent_version, args.log))
    else:
        report(args.dsn, args.log, args.out)


if __name__ == "__main__":
    main()
