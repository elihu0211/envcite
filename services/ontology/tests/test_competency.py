"""能力問題登記（competency.toml）與程式碼的一致性。

登記檔記下本體必須回答的問題與各題今天的實作現況。這裡守的是：登記的證據真的存在，
標「可答」的題真的有資料庫物件或 rpc 與測試，標「只有設計」的題沒有被偷偷承載。
這個測試不執行那些題，只驗證證據存在；執行靠被引用的測試本身。
"""

import re
import tomllib
from pathlib import Path

import psycopg

SERVICE = Path(__file__).resolve().parent.parent
REGISTRY = SERVICE / "competency.toml"
PROTO_DIR = SERVICE.parent.parent / "proto" / "envcite" / "ontology" / "v2"

#: 專論的七組三十三題。加題時，這裡與 competency.toml 同一次更新。
QUESTIONS = (
    [f"A{i}" for i in range(1, 6)]
    + [f"B{i}" for i in range(1, 6)]
    + [f"C{i}" for i in range(1, 9)]
    + [f"D{i}" for i in range(1, 4)]
    + [f"E{i}" for i in range(1, 4)]
    + [f"F{i}" for i in range(1, 3)]
    + [f"G{i}" for i in range(1, 8)]
)
STATUSES = {"answerable", "partial", "design_only"}
EVIDENCE_KEYS = ("db", "rpc", "field", "test")


def _questions() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["question"]
    return rows


def _proto_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(PROTO_DIR.glob("*.proto")))


def _strings(q: dict[str, object], key: str) -> list[str]:
    value = q.get(key, [])
    assert isinstance(value, list), f"{q['id']}.{key} 應為清單"
    return [str(v) for v in value]


def test_the_registry_covers_exactly_the_published_questions() -> None:
    ids = [str(q["id"]) for q in _questions()]
    assert sorted(ids) == sorted(QUESTIONS)
    assert len(ids) == len(set(ids)), "題號重複"


def test_every_question_has_a_known_status_and_only_known_evidence_keys() -> None:
    for q in _questions():
        assert q["status"] in STATUSES, q["id"]
        assert set(q) <= {"id", "status", *EVIDENCE_KEYS}, f"{q['id']} 有未知的鍵"


def test_an_answerable_question_has_a_mechanism_and_a_test() -> None:
    for q in _questions():
        if q["status"] != "answerable":
            continue
        mechanism = _strings(q, "db") + _strings(q, "rpc") + _strings(q, "field")
        assert mechanism, f"{q['id']} 標可答，卻沒有資料庫物件、rpc 或欄位"
        assert _strings(q, "test"), f"{q['id']} 標可答，卻沒有測試"


def test_a_design_only_question_has_no_evidence_at_all() -> None:
    for q in _questions():
        if q["status"] == "design_only":
            assert not any(_strings(q, k) for k in EVIDENCE_KEYS), f"{q['id']} 標只有設計，卻有證據"


def test_a_partial_question_has_something_built() -> None:
    for q in _questions():
        if q["status"] == "partial":
            assert any(_strings(q, k) for k in ("db", "rpc", "field")), (
                f"{q['id']} 標部分，卻沒有任何已建物件"
            )


def test_database_evidence_exists_in_the_migrated_schema(dsn: str) -> None:
    wanted = {name for q in _questions() for name in _strings(q, "db")}
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT n.nspname || '.' || c.relname FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'v', 'm', 'p')
            UNION
            SELECT n.nspname || '.' || p.proname FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
            """
        ).fetchall()
    have = {str(r[0]) for r in rows}
    assert not wanted - have, f"資料庫裡沒有：{sorted(wanted - have)}"


def test_rpc_and_field_evidence_exists_in_the_contract() -> None:
    proto = _proto_text()
    for q in _questions():
        for rpc in _strings(q, "rpc"):
            service, method = rpc.split(".")
            body = re.search(rf"service {service} \{{(.*?)\n\}}", proto, re.S)
            assert body and re.search(rf"rpc {method}\(", body.group(1)), (
                f"{q['id']}: 契約沒有 {rpc}"
            )
        for field in _strings(q, "field"):
            message, name = field.split(".")
            body = re.search(rf"(?:message|enum) {message} \{{(.*?)\n\}}", proto, re.S)
            assert body and re.search(rf"\b{name}\b", body.group(1)), f"{q['id']}: 契約沒有 {field}"


def test_test_evidence_names_a_test_that_exists() -> None:
    for q in _questions():
        for node in _strings(q, "test"):
            path, _, func = node.partition("::")
            file = SERVICE / path
            assert file.is_file(), f"{q['id']}: 沒有檔案 {path}"
            assert re.search(
                rf"^\s*def {re.escape(func)}\(", file.read_text(encoding="utf-8"), re.M
            ), f"{q['id']}: {path} 沒有 {func}"
