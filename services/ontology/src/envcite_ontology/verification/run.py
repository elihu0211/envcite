"""從條文到被接受的標註：抽取 → 驗證 → 接受。

全程無人。重跑冪等：同抽取者與版本已抽過的單元（以現行文字為準）不再抽，已有定論的閘門不再跑。
"""

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from envcite_ontology.db import Conn
from envcite_ontology.settings import DatabaseSettings
from envcite_ontology.settings import load as load_settings
from envcite_ontology.verification import definition_gates
from envcite_ontology.verification.attempts import record_attempt, reextract
from envcite_ontology.verification.config import ConfigError
from envcite_ontology.verification.config import load as load_models
from envcite_ontology.verification.definition_gates import DefinitionGate
from envcite_ontology.verification.definitions import (
    defined_terms,
    definition_paths,
    extract_definitions,
)
from envcite_ontology.verification.distinct import assert_distinct
from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.gates import Gate
from envcite_ontology.verification.link import held_states, link_penalties, penalized
from envcite_ontology.verification.methods import ALL_METHODS, gates_for, gates_from
from envcite_ontology.verification.models import Model
from envcite_ontology.verification.pipeline import Settled, settle
from envcite_ontology.verification.selection import (
    already_extracted,
    pending_laws,
    try_lock,
    units_of,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Run:
    """一次 `run` 的結果：處理的單元、抽到與丟掉的數量、接受結果、定義與抽取過程的計數。"""

    #: 處理的單元數，含略過的
    units: int
    #: 此抽取者與版本已抽過而略過的單元數
    skipped: int
    #: 抽到的陳述數，含事後連到被引用條文的罰則
    extracted: int
    #: 丟掉的輸出數，含定義與沒連成的罰則
    dropped: int
    settled: Settled
    linked: int = 0  # 事後連到被引用條文的罰則數（已含在 extracted）
    definitions: int = 0  # 抽到的用語定義數（不含在 extracted）
    definitions_confirmed: int = 0
    definitions_rejected: int = 0
    #: 陳述抽取的過程計數（不含用語定義與連結罰則的呼叫）
    calls: int = 0
    repairs: int = 0
    syntax_failures: int = 0
    shape_failures: int = 0


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
    definition_gates_: Sequence[DefinitionGate] | None = None,
    reextract_rounds: int = 0,
) -> Run:
    """對一批法規抽取、驗證、接受：先抽用語定義與陳述，再接受、重抽、連結罰則，最後接受定義。

    寫入資料庫並分段 commit；不取流程鎖，由呼叫端先 `try_lock`。`check_distinct` 為真時先對
    所有模型做別名檢查，同一個模型的兩個名字丟 `SameModel`。`required` 只管陳述；定義的必要
    方法固定為 `definition_gates.REQUIRED`。
    """
    if check_distinct:
        assert_distinct([extractor, *verifiers])
    units = skipped = extracted = dropped = definitions = 0
    calls = repairs = syntax_failures = shape_failures = 0
    for law in laws:
        # 先抽用語定義：陳述的要件要指名本法已定義的用語，兩邊代碼從源頭一致。
        # 定義項（「本法用語，定義如下」之下的款）不當規範抽取，只抽定義。
        in_definitions = set(definition_paths(conn, law))
        for path in units_of(conn, law):
            if path not in in_definitions:
                continue
            units += 1
            if already_extracted(conn, law, path, extractor.name, agent_version):
                skipped += 1
                continue
            found = extract_definitions(conn, extractor, law, path, agent_version=agent_version)
            record_attempt(conn, law, path, extractor.name, agent_version, found)
            definitions += found.loaded.definitions if found.loaded else 0
            dropped += len(found.dropped)
            for reason in found.dropped:
                log.warning("丟掉定義 %s %s：%s", law, path, reason[:300])
        terms = defined_terms(conn, law)
        punished = penalized(conn, law)
        for path in units_of(conn, law):
            if path in in_definitions:
                continue
            units += 1
            if already_extracted(conn, law, path, extractor.name, agent_version):
                skipped += 1
                continue
            out = extract_unit(
                conn,
                extractor,
                law,
                path,
                agent_version=agent_version,
                defined=terms,
                punished_by=punished.get(path, ()),
            )
            record_attempt(conn, law, path, extractor.name, agent_version, out)
            extracted += out.loaded.statements if out.loaded else 0
            calls += out.counts.calls
            repairs += out.counts.repairs
            syntax_failures += out.counts.syntax_failures
            shape_failures += out.counts.shape_failures
            dropped += len(out.dropped)
            for reason in out.dropped:
                log.warning("丟掉 %s %s：%s", law, path, reason[:300])
    chosen = gates if gates is not None else gates_for(verifiers, allow_same_family)
    # 綱要只讓罰則處罰已確認的義務：先接受，再把待連結罰則寫成陳述，再接受那些罰則。
    before = settle(conn, list(required), chosen)
    for _ in range(reextract_rounds):
        redone, again = reextract(conn, extractor, laws, agent_version, reextract_rounds)
        log.info("重抽：%d 個單元，%d 個陳述", redone, again)
        if not redone:
            break
        extracted += again
        after_round = settle(conn, list(required), chosen)
        before = Settled(
            before.confirmed + after_round.confirmed,
            before.rejected + after_round.rejected,
            before.rounds + after_round.rounds,
        )
    linked = link_penalties(conn, extractor)
    extracted += linked.statements
    dropped += len(linked.dropped)
    for reason in linked.dropped:
        log.warning("待連結的罰則沒連成：%s", reason[:300])
    after = settle(conn, list(required), chosen)
    log.info("待連結罰則：%s", held_states(conn))
    settled = Settled(
        before.confirmed + after.confirmed,
        before.rejected + after.rejected,
        before.rounds + after.rounds,
    )
    # 定義與陳述各自接受：必要方法固定是 literal 加往返，沒有往返的重讀者就不確認任何定義。
    chosen_defs = (
        definition_gates_
        if definition_gates_ is not None
        else definition_gates.gates_for(verifiers[0] if verifiers else None, allow_same_family)
    )
    defs = definition_gates.settle(conn, chosen_defs)
    return Run(
        units,
        skipped,
        extracted,
        dropped,
        settled,
        linked.statements,
        definitions,
        defs.confirmed,
        defs.rejected,
        calls,
        repairs,
        syntax_failures,
        shape_failures,
    )


def main() -> None:
    """`envcite-pipeline`：對 `--law` 指定或 `--pending` 挑出的法規跑完整流程，沒有子指令。

    `--check` 只確認設定檔與每個模型都通、沒有別名，不連資料庫。另一個流程持有鎖時不處理，
    直接結束；引數或設定檔不合以 `SystemExit` 結束。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-pipeline")
    parser.add_argument("--law", action="append", default=[], help="法規的 pcode，可重複")
    parser.add_argument(
        "--pending",
        action="store_true",
        help="處理還有單元未被此抽取者與版本抽過的現行法規（與 --law 擇一）",
    )
    parser.add_argument(
        "--limit", type=int, help="與 --pending 併用：一次最多處理幾部法規（控制模型費用）"
    )
    parser.add_argument("--models", type=Path, required=True, help="模型配置檔（models.toml）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只確認設定檔、端點、金鑰與每個模型都通（開跑前的同一個別名檢查），不連資料庫",
    )
    parser.add_argument("--agent-version", default="pipeline-1")
    parser.add_argument(
        "--reextract",
        type=int,
        choices=range(4),
        default=0,
        help="義務沒有定論的單元最多帶審查意見重抽幾輪（資料庫上限 3）",
    )
    parser.add_argument("--require", default=",".join(ALL_METHODS), help="必要的驗證方法，逗號分隔")
    parser.add_argument(
        "--allow-same-family", action="store_true", help="採計與抽取者同一家族的驗證者（不建議）"
    )
    parser.add_argument(
        "--no-distinct-check", action="store_true", help="略過「同一個模型的別名」檢查"
    )
    args = parser.parse_args()
    if not args.check:
        if bool(args.law) == args.pending:
            raise SystemExit("give --law (one or more) or --pending, not both and not neither")
        if args.limit is not None and (not args.pending or args.limit < 1):
            raise SystemExit("--limit is a positive number and goes with --pending")
    try:
        models = load_models(args.models)
    except ConfigError as e:
        raise SystemExit(f"{args.models}: {e}") from e
    if args.check:
        assert_distinct(models.everyone)
        log.info("通過：%s", "、".join(m.name for m in models.everyone))
        return
    settings = load_settings(DatabaseSettings)
    extractor, verifiers = models.extractor, list(models.verifiers)
    gates = gates_from(models, args.allow_same_family)
    definition_gates_ = definition_gates.gates_for(models.roundtrip, args.allow_same_family)
    log.info(
        "模型：抽取 %s；驗證 %s",
        extractor.name,
        "、".join(m.name for m in verifiers) or "（沒有）",
    )
    required = [m.strip() for m in args.require.split(",") if m.strip()]
    with psycopg.connect(settings.dsn) as conn:
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
            definition_gates_=definition_gates_,
            reextract_rounds=args.reextract,
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
    tokens = getattr(extractor, "tokens", None)
    logging.getLogger(__name__).info(
        "抽取計數：呼叫 %d，修復 %d，語法失敗 %d，形狀問題 %d；詞元 %s",
        result.calls,
        result.repairs,
        result.syntax_failures,
        result.shape_failures,
        tokens,
    )
