"""效能基準：延遲分佈、逐步加並行的吞吐與錯誤率，分冷熱快取（題庫外部，唯讀）。

    uv run --env-file <env> python tools/load_bench.py run <golden.yaml> \\
        --split eval/golden-split.json --pg-container <拋棄式容器> --out eval/load-bench-<日期>

一鍵跑完：本程式自己起 ontology 與 consult（Release）、以 k6（`tools/load.js`）送 gRPC 負載，
最後寫 `<out>.json` 與 `<out>.md`。對象與每個對象的量法：

- `search`：ontology `SearchArticles`（混合檢索，limit 與 consult 相同）
- `ground`：consult `Ground`（端到端：consult → ontology 混合檢索 → 可答性判定）
- `evaluate`：ontology `Evaluate`（推論；固定一件案件事實）
- `search-lexical`：`SearchArticles` 只開字面臂（伺服器不設模型），不呼叫嵌入端點，隔離資料庫的容量

每個對象依序量 `cold`（重啟資料庫容器與服務後循序一輪）、`hot`（循序暖一輪後再量一輪）與
`--ladder` 的每個並行度（shared-iterations，每階 `--iterations` 次）。`search-lexical` 不量冷。
冷只清得掉 PostgreSQL 的 shared_buffers 與服務行程的快取；Docker VM 的作業系統頁快取不清。

前提：`--pg-container` 是本機拋棄式的庫，**會被重啟**；資料庫由 ENVCITE_ONTOLOGY_DSN、
嵌入模型由 ENVCITE_ONTOLOGY_MODELS 給（同伺服器）。輸出不含題目文字，只有題號與數字；題目只經
環境變數交給 k6，不寫檔。混合檢索每次請求呼叫嵌入端點一次，總次數記在報告裡。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import grpc
import psycopg
from grpc_health.v1 import health_pb2, health_pb2_grpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs
from golden_retrieval_eval import _golden, git, snapshots

from envcite_ontology import server
from envcite_ontology.settings import DatabaseSettings, ModelsSettings, ServerSettings, load

HERE = Path(__file__).resolve().parent
SERVICE_DIR = HERE.parent
REPO = SERVICE_DIR.parents[1]
CONSULT_PROJECT = REPO / "services/consult/src/EnvCite.Consult"
K6_SCRIPT = HERE / "load.js"
PROTO_ROOT = REPO / "proto"

#: consult 對 ontology 的 limit（`OntologySearch.Limit`）；直接量 search 用同一個值才可對照。
SEARCH_LIMIT = 10
#: 推論的案件事實：概念不必在語料裡，量的是 Evaluate 的整條路徑（讀陳述、clingo、組回應）。
EVALUATE_REQUEST = {
    "conductOn": "2026-10-01",
    "decidedOn": "2026-10-15",
    "facts": [
        {"concept": "discharging", "values": [{"flag": True}]},
        {"concept": "cod", "values": [{"number": {"value": "150"}}]},
    ],
}
#: 期限比 consult 對 ontology 的 5 秒寬：逾時要記成 consult 自己回的狀態，不是 k6 先放棄。
TIMEOUT = "10s"
GRPC_STATUS = {
    0: "OK",
    1: "CANCELLED",
    2: "UNKNOWN",
    3: "INVALID_ARGUMENT",
    4: "DEADLINE_EXCEEDED",
    5: "NOT_FOUND",
    8: "RESOURCE_EXHAUSTED",
    13: "INTERNAL",
    14: "UNAVAILABLE",
}


@dataclass(frozen=True)
class Target:
    name: str
    method: str
    proto: str
    service: str  # "ontology" | "consult"
    embedding: bool
    cold: bool = True


TARGETS = {
    t.name: t
    for t in (
        Target(
            "search",
            "envcite.ontology.v1.OntologyService/SearchArticles",
            "envcite/ontology/v1/ontology.proto",
            "ontology",
            embedding=True,
        ),
        Target(
            "ground",
            "envcite.consult.v2.ConsultationService/Ground",
            "envcite/consult/v2/consult.proto",
            "consult",
            embedding=True,
        ),
        Target(
            "evaluate",
            "envcite.ontology.v1.ReasoningService/Evaluate",
            "envcite/ontology/v1/reasoning.proto",
            "ontology",
            embedding=False,
        ),
        Target(
            "search-lexical",
            "envcite.ontology.v1.OntologyService/SearchArticles",
            "envcite/ontology/v1/ontology.proto",
            "ontology",
            embedding=False,
            cold=False,
        ),
    )
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_serving(address: str, service: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with grpc.insecure_channel(address) as channel:
            try:
                reply = health_pb2_grpc.HealthStub(channel).Check(
                    health_pb2.HealthCheckRequest(service=service), timeout=2
                )
                if reply.status == health_pb2.HealthCheckResponse.SERVING:
                    return
            except grpc.RpcError:
                pass
        time.sleep(1)
    raise SystemExit(f"{address} 在 {timeout:.0f} 秒內沒有就緒")


def restart_database(container: str, dsn: str) -> None:
    subprocess.run(["docker", "restart", container], check=True, capture_output=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2):
                return
        except psycopg.OperationalError:
            time.sleep(1)
    raise SystemExit(f"{container} 重啟後 60 秒內連不上")


class Servers:
    """本程式起的 ontology 與 consult；`embedding` 決定 ontology 是否帶模型配置。"""

    def __init__(self, logs: Path) -> None:
        self._logs = logs
        self._procs: list[subprocess.Popen[bytes]] = []
        self.ontology = ""
        self.consult = ""
        self.embedding: bool | None = None

    def start(self, *, embedding: bool, consult: bool) -> None:
        self.stop()
        env = dict(os.environ)
        ontology_port = free_port()
        env["ENVCITE_ONTOLOGY_GRPC_PORT"] = str(ontology_port)
        if not embedding:
            env.pop("ENVCITE_ONTOLOGY_MODELS", None)
        self.ontology = f"127.0.0.1:{ontology_port}"
        self._spawn(["uv", "run", "envcite-ontology"], env, SERVICE_DIR, "ontology")
        wait_serving(self.ontology, server.SERVICE_NAME)
        self.embedding = embedding
        if consult:
            consult_port = free_port()
            env = dict(os.environ)
            env["ENVCITE_Ontology__Address"] = f"http://{self.ontology}"
            self.consult = f"127.0.0.1:{consult_port}"
            self._spawn(
                [
                    "dotnet",
                    "run",
                    "-c",
                    "Release",
                    "--no-build",
                    "--no-launch-profile",
                    "--project",
                    str(CONSULT_PROJECT),
                    "--",
                    "--urls",
                    f"http://{self.consult}",
                ],
                env,
                CONSULT_PROJECT,
                "consult",
            )
            wait_serving(self.consult, "")

    def _spawn(self, cmd: list[str], env: dict[str, str], cwd: Path, name: str) -> None:
        log = (self._logs / f"{name}.log").open("ab")
        self._procs.append(subprocess.Popen(cmd, env=env, cwd=cwd, stdout=log, stderr=log))

    def stop(self) -> None:
        for p in reversed(self._procs):
            p.terminate()
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        self._procs.clear()


@contextmanager
def servers(logs: Path) -> Iterator[Servers]:
    s = Servers(logs)
    try:
        yield s
    finally:
        s.stop()


def k6(
    target: Target,
    address: str,
    ids: Sequence[str],
    data: Sequence[dict[str, Any]],
    *,
    vus: int,
    iterations: int,
    work: Path,
) -> list[tuple[str, float, float, str]]:
    """跑一階，回傳每個請求的（題號、結束時刻、耗時秒、狀態）。"""
    out = work / "points.json"
    out.unlink(missing_ok=True)
    env = {
        **os.environ,
        "K6_NO_USAGE_REPORT": "true",
        "BENCH_DATA": json.dumps(list(data), ensure_ascii=False),
        "BENCH_IDS": json.dumps(list(ids)),
        "BENCH_METHOD": target.method,
        "BENCH_PROTO": target.proto,
        "BENCH_PROTO_ROOT": str(PROTO_ROOT),
        "BENCH_TARGET": address,
        "BENCH_TIMEOUT": TIMEOUT,
        "BENCH_VUS": str(vus),
        "BENCH_ITERATIONS": str(iterations),
    }
    subprocess.run(
        ["k6", "run", "--quiet", "--out", f"json={out}", str(K6_SCRIPT)],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    points = []
    with out.open(encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            if p.get("type") != "Point" or p.get("metric") != "grpc_req_duration":
                continue
            d = p["data"]
            end = datetime.fromisoformat(d["time"]).timestamp()
            tags = d.get("tags", {})
            status = GRPC_STATUS.get(int(tags.get("status", -1)), str(tags.get("status")))
            points.append((str(tags.get("qid")), end, float(d["value"]) / 1000, status))
    out.unlink()
    if len(points) != iterations:
        raise SystemExit(f"{target.name}：預期 {iterations} 筆，k6 只記到 {len(points)} 筆")
    return points


def percentile(values: Sequence[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def summarize(
    target: str, phase: str, vus: int, points: Sequence[tuple[str, float, float, str]]
) -> dict[str, Any]:
    secs = [p[2] for p in points]
    ok = [p[2] for p in points if p[3] == "OK"]
    started = min(p[1] - p[2] for p in points)
    wall = max(p[1] for p in points) - started
    statuses = Counter(p[3] for p in points)
    slowest = sorted(points, key=lambda p: p[2], reverse=True)[:5]
    return {
        "target": target,
        "phase": phase,
        "vus": vus,
        "n": len(points),
        "statuses": dict(statuses),
        "error_rate": 1 - statuses.get("OK", 0) / len(points),
        "p50": statistics.median(secs),
        "p95": percentile(secs, 0.95),
        "p99": percentile(secs, 0.99),
        "max": max(secs),
        "ok_p95": percentile(ok, 0.95) if ok else None,
        "wall_seconds": wall,
        "throughput_rps": len(points) / wall if wall > 0 else None,
        "ok_rps": len(ok) / wall if wall > 0 else None,
        "slowest": [{"id": p[0], "seconds": round(p[2], 4), "status": p[3]} for p in slowest],
    }


def requests(target: Target, questions: Sequence[tuple[str, str]]) -> tuple[list[str], list[Any]]:
    if target.name == "evaluate":
        return ["evaluate"], [EVALUATE_REQUEST]
    if target.name == "ground":
        return [q for q, _ in questions], [{"question": t} for _, t in questions]
    return [q for q, _ in questions], [{"query": t, "limit": SEARCH_LIMIT} for _, t in questions]


def environment(dsn: str, models: Path | None) -> dict[str, Any]:
    def sh(*cmd: str) -> str:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()

    docker = json.loads(sh("docker", "info", "--format", "{{json .}}"))
    with psycopg.connect(dsn) as conn:
        conn.read_only = True

        def one(sql: str) -> Any:
            row = conn.execute(sql).fetchone()
            assert row is not None
            return row[0]

        corpus = {
            "legal_resources": one("SELECT count(*) FROM corpus.legal_resource"),
            "provisions": one("SELECT count(*) FROM corpus.provision"),
            "passages": one("SELECT count(*) FROM retrieval.passage"),
            "embedded_passages": one(
                "SELECT count(*) FROM retrieval.passage WHERE embedding IS NOT NULL"
            ),
            "embedding_models": [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT model FROM retrieval.passage WHERE model IS NOT NULL"
                )
            ],
            "confirmed_statements": one(
                "SELECT count(*) FROM annotation.statement s JOIN annotation.assertion a"
                " ON a.id = s.id WHERE a.confirm_state = 'confirmed'"
            ),
            "database_size": one("SELECT pg_size_pretty(pg_database_size(current_database()))"),
            "snapshots": snapshots(conn),
        }
        pg = {
            "version": one("SHOW server_version"),
            "shared_buffers": one("SHOW shared_buffers"),
            "effective_cache_size": one("SHOW effective_cache_size"),
            "work_mem": one("SHOW work_mem"),
            "max_connections": one("SHOW max_connections"),
        }
    return {
        "host": {
            "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
            "cores": int(sh("sysctl", "-n", "hw.ncpu")),
            "memory_gib": int(sh("sysctl", "-n", "hw.memsize")) / 2**30,
            "os": f"{platform.system()} {platform.release()}",
        },
        "docker_vm": {
            "cpus": docker.get("NCPU"),
            "memory_gib": round(int(docker.get("MemTotal", 0)) / 2**30, 1),
            "server_version": docker.get("ServerVersion"),
        },
        "postgres": pg,
        "corpus": corpus,
        "servers": {
            "ontology_workers": server.WORKERS,
            "ontology_max_concurrent_rpcs": server.MAX_CONCURRENT_RPCS,
            "lexical_max_df": load(ServerSettings).lexical_max_df,
            "models": models.name if models else None,
            "consult_ontology_timeout": json.loads(
                (CONSULT_PROJECT / "appsettings.json").read_text(encoding="utf-8")
            )["Ontology"]["Timeout"],
            "search_limit": SEARCH_LIMIT,
        },
        "tools": {
            "k6": sh("k6", "version").splitlines()[0],
            "dotnet": sh("dotnet", "--version"),
            "python": platform.python_version(),
        },
    }


def run(args: argparse.Namespace) -> None:
    items, golden_sha = _golden(args.golden)
    split_doc = gs.read(args.split)
    gs.require_golden(split_doc, golden_sha, str(args.split))
    wanted = gs.subset_ids(split_doc, args.subset)
    questions = sorted((str(i["id"]), str(i["question"])) for i in items if str(i["id"]) in wanted)
    targets = [TARGETS[t] for t in args.targets.split(",")]
    ladder = [int(v) for v in args.ladder.split(",")]
    db = load(DatabaseSettings)
    models = load(ModelsSettings).models
    if any(t.embedding for t in targets) and models is None:
        raise SystemExit("混合檢索的對象需要 ENVCITE_ONTOLOGY_MODELS")

    if any(t.service == "consult" for t in targets):
        subprocess.run(
            ["dotnet", "build", "-c", "Release", "--nologo", "-v", "q", str(CONSULT_PROJECT)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    meta = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "envcite_commit": git("rev-parse", "HEAD"),
        "dirty": bool(
            git("status", "--porcelain", "--", str(REPO / "services"), str(REPO / "libs"))
        ),
        "golden": {"path": args.golden.name, "sha256": golden_sha},
        "split": {
            "path": args.split.name,
            "sha256": gs.file_sha256(args.split),
            "subset": args.subset,
        },
        "questions": len(questions),
        "ladder": ladder,
        "iterations_per_stage": args.iterations or len(questions),
        "timeout": TIMEOUT,
        **environment(db.dsn, models),
    }
    results: list[dict[str, Any]] = []
    embedding_calls = 0
    logs = Path(tempfile.mkdtemp(prefix="load-bench-"))
    print(f"服務日誌：{logs}", file=sys.stderr)
    with servers(logs) as s:
        for target in targets:
            ids, data = requests(target, questions)
            n = len(questions)
            consult = target.service == "consult"
            plan = [("warmup", 1, n), ("hot", 1, n)]
            plan += [("ladder", vus, args.iterations or n) for vus in ladder]
            if target.cold:
                s.stop()
                restart_database(args.pg_container, db.dsn)
                s.start(embedding=target.embedding, consult=consult)
                plan.insert(0, ("cold", 1, n))
            elif s.embedding is not target.embedding or (consult and not s.consult):
                s.start(embedding=target.embedding, consult=consult)
            address = s.consult if consult else s.ontology
            for phase, vus, iterations in plan:
                points = k6(target, address, ids, data, vus=vus, iterations=iterations, work=logs)
                if target.embedding:
                    embedding_calls += iterations
                if phase == "warmup":
                    continue
                r = summarize(target.name, phase, vus, points)
                results.append(r)
                print(
                    f"{target.name:<15}{phase:<6} c={vus:<3} n={r['n']:<4} "
                    f"p50={r['p50']:.3f} p95={r['p95']:.3f} p99={r['p99']:.3f} "
                    f"rps={r['throughput_rps']:.1f} err={r['error_rate']:.3f}",
                    file=sys.stderr,
                )
    doc = {"meta": {**meta, "embedding_calls": embedding_calls}, "results": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{args.out}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    Path(f"{args.out}.md").write_text(render(doc), encoding="utf-8")
    print(f"{args.out}.json、{args.out}.md；嵌入端點呼叫 {embedding_calls} 次", file=sys.stderr)


def render(doc: dict[str, Any]) -> str:
    m = doc["meta"]
    c = m["corpus"]
    lines = [
        f"# 效能基準（{m['run_at'][:10]}）",
        "",
        "`tools/load_bench.py` 產生；判讀見同名的 `.notes.md`。本機量測，**不代表部署環境**。",
        "",
        "## 條件",
        "",
        f"- commit `{m['envcite_commit']}`{'（工作樹有未提交的改動）' if m['dirty'] else ''}；"
        f"題庫 `{m['golden']['path']}` sha256 `{m['golden']['sha256'][:8]}…`，"
        f"{m['split']['subset']} 集 {m['questions']} 題",
        f"- 主機 {m['host']['cpu']}，{m['host']['cores']} 核，{m['host']['memory_gib']:.0f} GiB；"
        f"Docker VM {m['docker_vm']['cpus']} 核、{m['docker_vm']['memory_gib']} GiB",
        f"- PostgreSQL {m['postgres']['version']}，"
        f"shared_buffers {m['postgres']['shared_buffers']}，work_mem {m['postgres']['work_mem']}；"
        f"庫 {c['database_size']}",
        f"- 語料：法規 {c['legal_resources']:,}、條文單元 {c['provisions']:,}、"
        f"段落 {c['passages']:,}"
        f"（已嵌入 {c['embedded_passages']:,}，模型 {', '.join(c['embedding_models']) or '無'}）；"
        f"已確認的陳述 {c['confirmed_statements']}；快照 "
        + "、".join(f"{s['feed']} {s['data_date']}" for s in c["snapshots"]),
        f"- ontology 執行緒 {m['servers']['ontology_workers']}、同時 RPC 上限 "
        f"{m['servers']['ontology_max_concurrent_rpcs']}；lexical_max_df "
        f"{m['servers']['lexical_max_df']}；search limit {m['servers']['search_limit']}；"
        f"consult 對 ontology 期限 {m['servers']['consult_ontology_timeout']}；"
        f"請求期限 {m['timeout']}",
        f"- 每階 {m['iterations_per_stage']} 次請求；{m['tools']['k6']}；"
        f"嵌入端點呼叫 {m['embedding_calls']:,} 次",
        "",
        "## 結果",
        "",
        "秒；`rps` 是該階的請求數除以第一個請求開始到最後一個結束的牆鐘時間。",
        "",
        "| 對象 | 階段 | 並行 | n | p50 | p95 | p99 | max | rps | 錯誤率 | 非 OK |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|---|",
    ]
    for r in doc["results"]:
        bad = ", ".join(f"{k} {v}" for k, v in sorted(r["statuses"].items()) if k != "OK")
        lines.append(
            f"| {r['target']} | {r['phase']} | {r['vus']} | {r['n']} | {r['p50']:.3f} | "
            f"{r['p95']:.3f} | {r['p99']:.3f} | {r['max']:.3f} | {r['throughput_rps']:.1f} | "
            f"{r['error_rate']:.1%} | {bad or '—'} |"
        )
    lines += ["", "## 循序熱快取最慢的題", ""]
    for r in doc["results"]:
        if r["phase"] == "hot" and r["target"] != "evaluate":
            slow = "、".join(f"{x['id']} {x['seconds']:.2f}" for x in r["slowest"])
            lines.append(f"- {r['target']}：{slow}")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("golden", type=Path)
    r.add_argument("--split", type=Path, required=True)
    r.add_argument("--subset", default="dev")
    r.add_argument("--pg-container", required=True, help="本機拋棄式資料庫的容器名；量冷時會重啟")
    r.add_argument("--targets", default="search,ground,evaluate,search-lexical")
    r.add_argument("--ladder", default="1,2,4,8,16,24,32")
    r.add_argument("--iterations", type=int, default=0, help="每階請求數；0 為題數")
    r.add_argument("--out", type=Path, required=True)
    r.set_defaults(fn=run)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
