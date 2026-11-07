"""義務確認的稽核：兩位判定者回答兩個窄問題，量主體與適用範圍的實質瑕疵（唯讀）。

    # 以抽查報告的人工判斷校準（判定者要抓到 C、不誤抓 A）
    uv run python tools/duty_audit.py calibrate eval/duty-reextraction-2026-11-06.md \
        --dsn 'postgresql://…@127.0.0.1:55440/{db}' --out calib.json

    # 稽核一個抽取版本已確認的義務與禁止陳述
    uv run python tools/duty_audit.py run <dsn> <agent-version> --out audit-<版本>.json

    # 彙整多個稽核檔（同一條件的三輪放同一組），印出各條件的瑕疵率與輪間範圍
    uv run python tools/duty_audit.py report 條件甲=a.json,b.json,c.json 條件乙=d.json,e.json,f.json

判定者取自 `models.toml` 的 `[relevance]`，彼此不同家族；問題與選項固定，輸出是 JSON，快取以
（陳述內容、判定者、提示雜湊）為鍵，所以重跑不重花費用。不是閘門：它不寫資料庫，也不影響確認。
金鑰從作業系統鑰匙圈取（見 `verification/config.py`）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psycopg

from envcite_ontology.verification.config import load_judges
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model

DUTY = "s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')"

SUBJECT_CHOICES = {
    "actor": "主體是原文寫出的、負有這個義務的人或組織",
    "unstated_ok": "主體標為未明示，原文確實沒有寫出義務人",
    "thing": "主體寫成被規範的物、設施、物質、水或行為（不是人或組織）",
    "invented": "主體是原文沒有的字（例如補上「任何人」「行為人」）",
    "missed_actor": "主體標為未明示，但原文其實寫出了義務人",
    "wrong_actor": "主體是人或組織，但不是這個義務的義務人",
}
SUBJECT_DEFECT = {"thing", "invented", "missed_actor", "wrong_actor"}

PROMPT = """你是法規標註的稽核員。下面是一個條文單元的原文（含上層條文），與一個義務陳述的標註。
只回答兩個問題，只輸出 JSON。

問題一，主體：標註的「主體」欄屬於下列哪一種？
{choices}

問題二，適用範圍：標註對應的引文所在的句子（含上層條文的導言）裡，有沒有改變這個義務適用
範圍的條件，沒有出現在標註的「適用範圍」裡？條件包括：引文之前同一句的條件子句、上層導言的
條件（例如「在水污染管制區內」）。標有「承自上位」的適用範圍要件是程式從原文帶入的，算已出現。
另外，引文有沒有停在並列的行為或替代方式之前（句子後面還有「或…」「並…」而引文沒有包含），
使義務變窄？

輸出：{{"subject": "上列選項的代號", "missing_condition": true 或 false,
 "missing_text": "缺少的原文，沒有則空字串", "truncated": true 或 false}}

{context}單元原文（{unit}）：
{text}

標註對應的引文：
{quote}

標註：
{annotation}
"""


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


def _columns(conn: Any) -> set[str]:
    return {
        f"{t}.{c}"
        for t, c in conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'annotation' AND table_name IN"
            " ('statement_slot', 'statement_condition')"
        )
    }


def record(conn: Any, sid: int) -> dict[str, Any]:
    """一個陳述給判定者看的全部內容。"""
    cols = _columns(conn)
    unstated = "sl.unstated" if "statement_slot.unstated" in cols else "false"
    carried = "c.carried" if "statement_condition.carried" in cols else "false"
    row = conn.execute(
        "SELECT r.local_id, p.path, p.id, v.content, lower(s.span), upper(s.span),"
        "       s.norm_modality::text, a.confirm_state::text, d.producer_version"
        "  FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
        "  JOIN annotation.derivation d ON d.id = a.derivation_id"
        "  JOIN corpus.provision p ON p.id = s.provision_id"
        "  JOIN corpus.legal_resource r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256"
        " WHERE s.id = %s",
        (sid,),
    ).fetchone()
    law, path, pid, text, lo, hi, modality, state, version = row
    subjects = [
        "（原文未明示）" if unst else str(name)
        for name, unst in conn.execute(
            f"SELECT coalesce(k.name, sl.literal), {unstated}"
            " FROM annotation.statement_slot sl LEFT JOIN vocab.concept k ON k.id = sl.concept_id"
            " WHERE sl.statement_id = %s AND sl.slot_role = 'subject' ORDER BY sl.alternative",
            (sid,),
        )
    ]
    conds: dict[str, list[str]] = defaultdict(list)
    for role, name, neg, carr in conn.execute(
        f"SELECT c.condition_role::text, k.name, c.negated, {carried}"
        " FROM annotation.statement_condition c JOIN vocab.concept k ON k.id = c.concept_id"
        " WHERE c.statement_id = %s ORDER BY c.condition_role, c.disjunct_no, c.id",
        (sid,),
    ):
        conds[str(role)].append(
            ("非：" if neg else "") + str(name) + ("（承自上位）" if carr else "")
        )
    return {
        "id": sid,
        "key": str(sid),
        "unit": f"{law} {path}",
        "version": version,
        "state": state,
        "text": str(text),
        "context": _context(conn, int(pid)),
        "quote": str(text) if lo is None else str(text)[lo:hi],
        "annotation": "\n".join(
            [
                f"義務方向：{'義務' if modality == 'obligation' else '禁止'}",
                f"主體：{'；'.join(subjects) if subjects else '（沒有主體欄）'}",
                f"適用範圍（皆須成立）：{'；'.join(conds['scope']) or '（無）'}",
                f"義務內容：{'；'.join(conds['duty']) or '（無）'}",
            ]
        ),
    }


def prompt_of(rec: Mapping[str, Any]) -> str:
    context = f"上層條文：\n{rec['context']}\n\n" if rec["context"] else ""
    choices = "\n".join(f"- {k}：{v}" for k, v in SUBJECT_CHOICES.items())
    return PROMPT.format(
        choices=choices,
        context=context,
        unit=rec["unit"],
        text=rec["text"],
        quote=rec["quote"],
        annotation=rec["annotation"],
    )


def ask(judge: Model, rec: Mapping[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    prompt = prompt_of(rec)
    key = hashlib.sha256(f"{judge.name}\n{prompt}".encode()).hexdigest()
    if key in cache:
        return dict(cache[key])
    answer = object_from(judge.complete(prompt)) or {}
    subject = str(answer.get("subject", ""))
    out = {
        "subject": subject if subject in SUBJECT_CHOICES else "unparsed",
        "missing_condition": answer.get("missing_condition") is True,
        "missing_text": str(answer.get("missing_text") or ""),
        "truncated": answer.get("truncated") is True,
    }
    cache[key] = out
    return out


def defects(answer: Mapping[str, Any]) -> tuple[bool, bool]:
    """（主體瑕疵，適用範圍瑕疵）。"""
    return answer["subject"] in SUBJECT_DEFECT, bool(
        answer["missing_condition"] or answer["truncated"]
    )


def judge_all(
    records: Sequence[Mapping[str, Any]], judges: Sequence[Model], cache_path: Path
) -> list[dict[str, Any]]:
    cache: dict[str, Any] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    jobs = [(r, j) for r in records for j in judges]
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(lambda rj: ask(rj[1], rj[0], cache), jobs))
    cache_path.write_text(json.dumps(cache, ensure_ascii=False))
    by_key: dict[str, dict[str, Any]] = {}
    for (rec, judge), answer in zip(jobs, answers, strict=True):
        by_key.setdefault(str(rec["key"]), {**rec, "answers": {}})["answers"][judge.name] = answer
    return list(by_key.values())


# 校準：抽查報告的人工判斷

_ROW = re.compile(
    r"^\|\s*(?:隨機|關鍵補充)\s*\|\s*(?P<ref>[a-z0-9]+-\d+:\d+)\s*\|.*\|\s*(?P<label>[ABCD])\s*\|"
    r"\s*(?P<why>.*?)\s*\|\s*$"
)


def labelled(report: Path) -> list[tuple[str, int, str, str]]:
    """（庫名, 陳述編號, 判斷, 理由）。版本 `c2-1` 的庫是 `c2_1`，`base-1` 的庫是 `b1`。"""
    out = []
    for line in report.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line)
        if m is None:
            continue
        version, sid = m["ref"].split(":")
        db = "b" + version[-1] if version.startswith("base") else version.replace("-", "_")
        out.append((db, int(sid), m["label"], m["why"]))
    return out


def kind_of(label: str, why: str) -> str:
    """人工判斷的 C 屬哪一型：主體、適用範圍，或其他（槽位錯置）。"""
    if label != "C":
        return label
    if "主體" in why:
        return "C-subject"
    if re.search(r"漏|截|上位|句首|替代", why):
        return "C-context"
    return "C-other"


def calibrate(args: argparse.Namespace) -> None:
    judges = list(load_judges(Path(args.models)).models)
    rows = labelled(Path(args.report))
    records, kinds = [], {}
    conns: dict[str, Any] = {}
    for db, sid, label, why in rows:
        conn = conns.setdefault(db, psycopg.connect(args.dsn.format(db=db)))
        rec = record(conn, sid) | {"key": f"{db}:{sid}"}
        records.append(rec)
        kinds[rec["key"]] = kind_of(label, why)
    results = judge_all(records, judges, Path(args.out).with_suffix(".cache.json"))
    table: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for res in results:
        kind = kinds[str(res["key"])]
        for name, answer in res["answers"].items():
            subj, ctx = defects(answer)
            t = table[kind][name]
            t[0] += 1
            t[1] += int(subj)
            t[2] += int(ctx)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("| 人工判斷 | 判定者 | 筆數 | 判主體瑕疵 | 判適用範圍瑕疵 |\n|---|---|---|---|---|")
    for kind in sorted(table):
        for name, (n, subj, ctx) in sorted(table[kind].items()):
            print(f"| {kind} | {name} | {n} | {subj} | {ctx} |")


# 稽核


def run(args: argparse.Namespace) -> None:
    judges = list(load_judges(Path(args.models)).models)
    with psycopg.connect(args.dsn) as conn:
        ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT s.id FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
                " JOIN annotation.derivation d ON d.id = a.derivation_id"
                f" WHERE {DUTY} AND a.confirm_state = 'confirmed' AND d.producer_version = %s"
                " ORDER BY s.id",
                (args.version,),
            )
        ]
        if args.n and args.n < len(ids):
            ids = sorted(random.Random(args.seed).sample(ids, args.n))
        records = [record(conn, i) for i in ids]
    results = judge_all(records, judges, Path(args.out).with_suffix(".cache.json"))
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"{args.version}: {len(results)} 筆，判定者 {[j.name for j in judges]}")


def kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def report(args: argparse.Namespace) -> None:
    print(
        "| 條件 | 輪 | 確認的義務陳述 | 主體瑕疵（兩位都判）| 主體瑕疵（任一位）|"
        " 適用範圍瑕疵（兩位都判）| 適用範圍瑕疵（任一位）|"
    )
    print("|---|---|---|---|---|---|---|")
    for spec in args.groups:
        name, files = spec.split("=", 1)
        per_round = []
        for i, f in enumerate(files.split(","), 1):
            rows = json.loads(Path(f).read_text())
            both_s = any_s = both_c = any_c = 0
            for r in rows:
                flags = [defects(a) for a in r["answers"].values()]
                both_s += all(s for s, _ in flags)
                any_s += any(s for s, _ in flags)
                both_c += all(c for _, c in flags)
                any_c += any(c for _, c in flags)
            per_round.append((len(rows), both_s, any_s, both_c, any_c))
            print(f"| {name} | {i} | {len(rows)} | {both_s} | {any_s} | {both_c} | {any_c} |")
        n = sum(x[0] for x in per_round)
        rates = [[x[k] / x[0] for x in per_round if x[0]] for k in range(1, 5)]
        print(
            f"| **{name}** | 合計 | {n} | "
            + " | ".join(
                f"{sum(x[k] for x in per_round)}（{min(r):.0%}–{max(r):.0%}）"
                for k, r in zip(range(1, 5), rates, strict=True)
            )
            + " |"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    cal = sub.add_parser("calibrate")
    cal.add_argument("report")
    cal.add_argument("--dsn", required=True, help="含 {db} 的連線字串")
    cal.add_argument("--models", default="models.toml")
    cal.add_argument("--out", required=True)
    one = sub.add_parser("run")
    one.add_argument("dsn")
    one.add_argument("version")
    one.add_argument("--models", default="models.toml")
    one.add_argument("--out", required=True)
    one.add_argument("--n", type=int, default=0, help="只稽核隨機 n 筆（種子固定）")
    one.add_argument("--seed", type=int, default=1)
    rep = sub.add_parser("report")
    rep.add_argument("groups", nargs="+", help="條件=檔1,檔2,檔3")
    args = parser.parse_args()
    {"calibrate": calibrate, "run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
