"""從條文到被接受的標註，整條線：抽取 → 驗證 → 接受（env-law-docs 02-05 §5.9）。

線上沒有人。抽取器與驗證者都是 `Model`；準確度由驗證與校準負責，不由這裡保證。重跑是冪等的：
同一個抽取者與版本已經抽過的單元（以現行文字為準）不再抽；已有定論的閘門不再跑。
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from env_law_ontology.verification.extract import extract_unit
from env_law_ontology.verification.gates import DEFAULT_GATES, Gate
from env_law_ontology.verification.model_gates import IndependentTestGate, PanelGate, RoundtripGate
from env_law_ontology.verification.models import Model, build_model
from env_law_ontology.verification.pipeline import Settled, settle

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

ALL_METHODS = ("numbers", "references", "executable", "roundtrip", "independent_test", "panel")


#: 兩個「不同」的模型對這些開放式問題在溫度 0 下逐字相同，幾乎必定是同一個模型的兩個名字。
PROBES = ("用兩句話說明什麼是法律的位階，要有你自己的措辭。", "寫一首四句的詩，主題是河川。")


class SameModel(ValueError):
    """兩個名字背後是同一個模型：它們不獨立，不能互相驗證。"""


def assert_distinct(models: Sequence[Model]) -> None:
    """託管服務的模型名稱不保證是它所指的模型（同一個模型常有幾個別名），名字不同不等於家族不同。

    對每個模型問同樣的開放式問題，逐字相同的就是同一個模型。這只抓得到完全相同的別名，抓不到
    同一個基礎模型的不同微調；而且只有在溫度為 0 的轉接器上有效（`chat` 轉接器送溫度 0）。
    """
    answers = {m.name: tuple(m.complete(p) for p in PROBES) for m in models}
    names = list(answers)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if answers[a] == answers[b]:
                raise SameModel(
                    f"{a} and {b} answered every probe identically: same model under two names"
                )


@dataclass(frozen=True)
class Run:
    units: int
    skipped: int
    extracted: int
    dropped: int
    settled: Settled


def units_of(conn: Conn, law: str) -> list[str]:
    """法規現行、有文字的單元路徑，依閱讀順序。"""
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT p.path FROM corpus.provision AS p"
            "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
            " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND EXISTS ("
            "   SELECT 1 FROM corpus.provision_version AS v"
            "    WHERE v.provision_id = p.id AND v.superseded_at IS NULL AND v.content IS NOT NULL)"
            " ORDER BY p.sort_key, p.path",
            (law,),
        )
    ]


def _already_extracted(conn: Conn, law: str, path: str, agent: str, version: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM annotation.statement AS s"
        "  JOIN annotation.derivation AS d ON d.id = s.derivation_id"
        "  JOIN corpus.provision AS p ON p.id = s.provision_id"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE r.local_id = %s AND p.path = %s AND d.agent = %s AND d.agent_version = %s LIMIT 1",
        (law, path, agent, version),
    ).fetchone()
    return row is not None


def gates_for(verifiers: Sequence[Model], allow_same_family: bool = False) -> tuple[Gate, ...]:
    """確定性閘門，加上以驗證者組成的模型閘門：往返用第一個，獨立測試用最後一個，評審團用全部。"""
    if not verifiers:
        return DEFAULT_GATES
    return (
        *DEFAULT_GATES,
        RoundtripGate(verifiers[0], allow_same_family),
        IndependentTestGate(verifiers[-1], allow_same_family),
        PanelGate(tuple(verifiers), allow_same_family=allow_same_family),
    )


def run(
    conn: Conn,
    extractor: Model,
    verifiers: Sequence[Model],
    laws: Sequence[str],
    *,
    agent_version: str,
    required: Sequence[str] = ALL_METHODS,
    allow_same_family: bool = False,
    check_distinct: bool = False,
) -> Run:
    if check_distinct:
        assert_distinct([extractor, *verifiers])
    units = skipped = extracted = dropped = 0
    for law in laws:
        for path in units_of(conn, law):
            units += 1
            if _already_extracted(conn, law, path, extractor.name, agent_version):
                skipped += 1
                continue
            out = extract_unit(conn, extractor, law, path, agent_version=agent_version)
            extracted += out.loaded.statements if out.loaded else 0
            dropped += len(out.dropped)
            for reason in out.dropped:
                log.warning("丟掉 %s %s：%s", law, path, reason[:300])
    settled = settle(conn, list(required), gates_for(verifiers, allow_same_family))
    return Run(units, skipped, extracted, dropped, settled)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="env-law-pipeline")
    parser.add_argument("--law", action="append", required=True, help="法規的 pcode，可重複")
    parser.add_argument(
        "--extractor",
        required=True,
        help="anthropic:<模型> 或 chat:<供應商/模型>，例如 anthropic:claude-sonnet-5-5",
    )
    parser.add_argument(
        "--verifier",
        action="append",
        default=[],
        help="驗證者，可重複；與抽取者不同家族才算數，例如 chat:google/gemma-4-31b",
    )
    parser.add_argument("--agent-version", default="pipeline-1")
    parser.add_argument("--require", default=",".join(ALL_METHODS), help="必要的驗證方法，逗號分隔")
    parser.add_argument(
        "--allow-same-family", action="store_true", help="採計與抽取者同一家族的驗證者（不建議）"
    )
    parser.add_argument(
        "--no-distinct-check", action="store_true", help="略過「同一個模型的別名」檢查"
    )
    args = parser.parse_args()
    dsn = os.environ.get("ONTOLOGY_DSN", "")
    if not dsn:
        raise SystemExit("ONTOLOGY_DSN is required")
    extractor = build_model(args.extractor)
    verifiers = [build_model(v) for v in args.verifier]
    required = [m.strip() for m in args.require.split(",") if m.strip()]
    with psycopg.connect(dsn) as conn:
        result = run(
            conn,
            extractor,
            verifiers,
            args.law,
            agent_version=args.agent_version,
            required=required,
            allow_same_family=args.allow_same_family,
            check_distinct=not args.no_distinct_check,
        )
    logging.getLogger(__name__).info(
        "單元 %d（略過 %d），抽取 %d 個陳述（丟掉 %d）；%d 個確認、%d 個駁回，%d 輪",
        result.units,
        result.skipped,
        result.extracted,
        result.dropped,
        result.settled.confirmed,
        result.settled.rejected,
        result.settled.rounds,
    )
