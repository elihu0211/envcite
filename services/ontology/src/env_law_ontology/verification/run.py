"""從條文到被接受的標註：抽取 → 驗證 → 接受（env-law-docs 02-06 §3.3）。

全程無人。重跑冪等：同抽取者與版本已抽過的單元（以現行文字為準）不再抽，已有定論的閘門不再跑。
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from env_law_ontology.verification.config import ConfigError, ModelSet
from env_law_ontology.verification.config import load as load_models
from env_law_ontology.verification.extract import Extracted, extract_unit
from env_law_ontology.verification.gates import DEFAULT_GATES, Gate
from env_law_ontology.verification.link import link_penalties
from env_law_ontology.verification.model_gates import IndependentTestGate, PanelGate, RoundtripGate
from env_law_ontology.verification.models import Model
from env_law_ontology.verification.pipeline import Settled, settle

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

ALL_METHODS = (
    "numbers",
    "references",
    "executable",
    "provisos",
    "roundtrip",
    "independent_test",
    "panel",
)


#: 溫度 0 下逐字相同的回答，幾乎必定是同一個模型的兩個名字。
PROBES = ("用兩句話說明什麼是法律的位階，要有你自己的措辭。", "寫一首四句的詩，主題是河川。")


class SameModel(ValueError):
    """兩個名字背後是同一個模型，不獨立，不能互相驗證。"""


def assert_distinct(models: Sequence[Model]) -> None:
    """對每個模型問同樣的開放式問題，逐字相同即同一模型（託管服務常有別名）。

    只抓得到完全相同的別名，抓不到同基礎模型的微調；僅在溫度為 0 的轉接器上有效。
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
    linked: int = 0  # 事後連到被引用條文的罰則數（已含在 extracted）


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


#: 同一時間只讓一個流程花模型費用；鍵是任意固定常數，跨連線共用。
_LOCK_KEY = 0x656E764C  # "envL"


def try_lock(conn: Conn) -> bool:
    """取得流程的諮詢鎖；已有另一個流程在跑時回 False。鎖隨連線結束釋放。"""
    row = conn.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,)).fetchone()
    return bool(row and row[0])


def pending_laws(conn: Conn, agent: str, version: str, limit: int | None = None) -> list[str]:
    """還有單元未被此抽取者與版本抽過的現行法規 pcode，依 pcode 排序。

    待辦由資料推得，不另存佇列：單元的現行文字被抽過（有同抽取者與版本的陳述錨在它的雜湊上）
    就不算待辦，所以文字更新後單元自動回到待辦。`limit` 限制一次處理幾部法規，用來控制費用。
    """
    rows = conn.execute(
        "SELECT r.local_id FROM corpus.legal_resource AS r"
        " WHERE r.id_scheme = 'moj_pcode' AND r.repealed_on IS NULL AND EXISTS ("
        "   SELECT 1 FROM corpus.provision AS p"
        "     JOIN corpus.provision_version AS v ON v.provision_id = p.id"
        "    WHERE p.resource_id = r.id AND v.superseded_at IS NULL AND v.content IS NOT NULL"
        "      AND NOT EXISTS ("
        "        SELECT 1 FROM annotation.statement AS s"
        "          JOIN annotation.derivation AS d ON d.id = s.derivation_id"
        "         WHERE s.provision_id = p.id AND s.content_sha256 = v.content_sha256"
        "           AND d.producer = %(agent)s AND d.producer_version = %(version)s)"
        "      AND NOT EXISTS ("
        "        SELECT 1 FROM annotation.extraction_attempt AS a"
        "         WHERE a.provision_id = p.id AND a.content_sha256 = v.content_sha256"
        "           AND a.producer = %(agent)s AND a.producer_version = %(version)s))"
        " ORDER BY r.local_id LIMIT %(limit)s",
        {"agent": agent, "version": version, "limit": limit},
    )
    return [str(row[0]) for row in rows]


def _already_extracted(conn: Conn, law: str, path: str, agent: str, version: str) -> bool:
    """此抽取者與版本已對單元的現行文字答過：有陳述錨在它，或留有抽取紀錄（可能一個陳述也沒有）。"""
    row = conn.execute(
        "SELECT 1 FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        " WHERE r.local_id = %(law)s AND p.path = %(path)s AND ("
        "   EXISTS (SELECT 1 FROM annotation.statement AS s"
        "             JOIN annotation.derivation AS d ON d.id = s.derivation_id"
        "            WHERE s.provision_id = p.id AND s.content_sha256 = v.content_sha256"
        "              AND d.producer = %(agent)s AND d.producer_version = %(version)s)"
        "   OR EXISTS (SELECT 1 FROM annotation.extraction_attempt AS a"
        "               WHERE a.provision_id = p.id AND a.content_sha256 = v.content_sha256"
        "                 AND a.producer = %(agent)s AND a.producer_version = %(version)s))"
        " LIMIT 1",
        {"law": law, "path": path, "agent": agent, "version": version},
    ).fetchone()
    return row is not None


#: 沒有答案可記的結果：模型沒回或答不成 JSON，下次再試。
_NO_ANSWER = ("<model error", "<unparseable model output>")


def _record_attempt(
    conn: Conn, law: str, path: str, agent: str, version: str, out: Extracted
) -> None:
    if any(reason.startswith(_NO_ANSWER) for reason in out.dropped):
        return
    conn.execute(
        "INSERT INTO annotation.extraction_attempt"
        "  (provision_id, content_sha256, producer, producer_version, statements, dropped)"
        " SELECT p.id, v.content_sha256, %s, %s, %s, %s"
        "   FROM corpus.provision AS p"
        "   JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "   JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        "  WHERE r.local_id = %s AND p.path = %s"
        " ON CONFLICT DO NOTHING",
        (agent, version, out.loaded.statements if out.loaded else 0, len(out.dropped), law, path),
    )
    conn.commit()


def gates_for(verifiers: Sequence[Model], allow_same_family: bool = False) -> tuple[Gate, ...]:
    """確定性閘門加模型閘門：往返用第一個驗證者，獨立測試用最後一個，評審團用全部。"""
    if not verifiers:
        return DEFAULT_GATES
    return (
        *DEFAULT_GATES,
        RoundtripGate(verifiers[0], allow_same_family),
        IndependentTestGate(verifiers[-1], allow_same_family),
        PanelGate(tuple(verifiers), allow_same_family=allow_same_family),
    )


def gates_from(models: ModelSet, allow_same_family: bool = False) -> tuple[Gate, ...]:
    """確定性閘門加模型閘門，角色（重抽、設計測試、評審團）由設定檔明定。"""
    return (
        *DEFAULT_GATES,
        RoundtripGate(models.roundtrip, allow_same_family),
        IndependentTestGate(models.independent_test, allow_same_family),
        PanelGate(models.panel, allow_same_family=allow_same_family),
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
    gates: Sequence[Gate] | None = None,
) -> Run:
    if check_distinct:
        assert_distinct([extractor, *verifiers])
    units = skipped = extracted = dropped = 0
    pending: list[Mapping[str, Any]] = []
    for law in laws:
        for path in units_of(conn, law):
            units += 1
            if _already_extracted(conn, law, path, extractor.name, agent_version):
                skipped += 1
                continue
            out = extract_unit(conn, extractor, law, path, agent_version=agent_version)
            _record_attempt(conn, law, path, extractor.name, agent_version, out)
            extracted += out.loaded.statements if out.loaded else 0
            dropped += len(out.dropped)
            pending += out.pending
            for reason in out.dropped:
                log.warning("丟掉 %s %s：%s", law, path, reason[:300])
    linked = link_penalties(conn, extractor, pending)
    extracted += linked.statements
    dropped += len(linked.dropped)
    for reason in linked.dropped:
        log.warning("丟掉待連結的罰則：%s", reason[:300])
    chosen = gates if gates is not None else gates_for(verifiers, allow_same_family)
    settled = settle(conn, list(required), chosen)
    return Run(units, skipped, extracted, dropped, settled, linked.statements)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="env-law-pipeline")
    parser.add_argument("--law", action="append", default=[], help="法規的 pcode，可重複")
    parser.add_argument(
        "--pending",
        action="store_true",
        help="處理還有單元未被此抽取者與版本抽過的現行法規（與 --law 擇一）",
    )
    parser.add_argument(
        "--limit", type=int, help="與 --pending 併用：一次最多處理幾部法規（控制模型費用）"
    )
    parser.add_argument(
        "--models", type=Path, required=True, help="模型配置檔（TOML，範本 models.example.toml）"
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
    if bool(args.law) == args.pending:
        raise SystemExit("give --law (one or more) or --pending, not both and not neither")
    if args.limit is not None and (not args.pending or args.limit < 1):
        raise SystemExit("--limit is a positive number and goes with --pending")
    dsn = os.environ.get("ONTOLOGY_DSN", "")
    if not dsn:
        raise SystemExit("ONTOLOGY_DSN is required")
    try:
        models = load_models(args.models)
    except ConfigError as e:
        raise SystemExit(f"{args.models}: {e}") from e
    extractor, verifiers = models.extractor, list(models.verifiers)
    gates = gates_from(models, args.allow_same_family)
    log.info(
        "模型：抽取 %s；驗證 %s",
        extractor.name,
        "、".join(m.name for m in verifiers) or "（沒有）",
    )
    required = [m.strip() for m in args.require.split(",") if m.strip()]
    with psycopg.connect(dsn) as conn:
        if not try_lock(conn):
            log.info("另一個流程正在跑，這次不處理")
            return
        laws = (
            pending_laws(conn, extractor.name, args.agent_version, args.limit)
            if args.pending
            else args.law
        )
        log.info("法規 %d 部：%s", len(laws), "、".join(laws[:20]))
        result = run(
            conn,
            extractor,
            verifiers,
            laws,
            agent_version=args.agent_version,
            required=required,
            allow_same_family=args.allow_same_family,
            check_distinct=not args.no_distinct_check,
            gates=gates,
        )
    logging.getLogger(__name__).info(
        "單元 %d（略過 %d），抽取 %d 個陳述（含連結的罰則 %d 個；丟掉 %d）；"
        "%d 個確認、%d 個駁回，%d 輪",
        result.units,
        result.skipped,
        result.extracted,
        result.linked,
        result.dropped,
        result.settled.confirmed,
        result.settled.rejected,
        result.settled.rounds,
    )
