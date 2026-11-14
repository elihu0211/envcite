"""公報的廢止：「廢止「甲」」停止此前刊登甲的各件。"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, NamedTuple

from envcite_ontology import guidance
from envcite_ontology.acquire import gazette
from envcite_ontology.db import Conn, fetch, fetch_one
from envcite_ontology.guidance_sync.problems import REPEAL_PROBLEMS, ItemRow, record_problems
from envcite_ontology.guidance_sync.provenance import DERIVATION
from envcite_ontology.guidance_sync.report import Report
from envcite_ontology.import_problems import Problem

log = logging.getLogger(__name__)


class _GazetteRow(NamedTuple):
    id: int
    issuer: str
    issued_on: date
    subject: str
    ceased: bool


def apply_repeals(conn: Conn, report: Report) -> None:
    """公報的「廢止「甲」」停止此前刊登甲的各件（只在公報的行政規則之間比對名稱）。"""
    docs = fetch(
        conn,
        "SELECT d.id, d.issuer, d.issued_on, d.subject,"
        "       EXISTS (SELECT 1 FROM guidance.cessation AS c WHERE c.document_id = d.id)"
        "  FROM guidance.document AS d WHERE starts_with(d.source_url, %s)"
        " ORDER BY d.issued_on, d.id",
        (gazette.BASE,),
        _GazetteRow,
    )
    conn.rollback()
    actions = {r.id: gazette.actions(r.subject) for r in docs}
    issuer = {r.id: r.issuer for r in docs}
    issued = {r.id: r.issued_on for r in docs}
    ceased = {r.id for r in docs if r.ceased}
    rules = _RuleNames()
    for doc_id, a in actions.items():
        rules.add(doc_id, a.sets, a.renames)
    for doc_id, a in actions.items():
        if not a.repeals:
            continue
        problems: list[Problem] = []
        earlier: set[int] = set()
        for name in a.repeals:
            found = {
                d for d in rules.documents(name) if issued[d] <= issued[doc_id] and d != doc_id
            }
            if not found:
                problems.append(Problem("warning", "repeal_unmatched", name))
            elif len({issuer[d] for d in found}) > 1:
                problems.append(Problem("warning", "repeal_ambiguous", name))
            else:
                earlier |= found
        on = a.effective_on
        if on is not None and on < issued[doc_id]:
            problems.append(Problem("warning", "repeal_before_issue", on.isoformat()))
            on = None
        pending = sorted(earlier - ceased)
        if pending:
            document = {
                **_loaded(conn, doc_id),
                "supersedes": [
                    {**_ref(conn, d), **({"from": on.isoformat()} if on else {})} for d in pending
                ],
            }
            loaded = _reload(conn, document)
            if loaded < 0:
                problems.append(Problem("warning", "repeal_rejected", "、".join(a.repeals)))
            else:
                # 同一條規則再被後一件廢止時，已停止的不再寫（停止適用只記最早的一次）
                ceased.update(pending)
                report.cessations += loaded
        _repeal_problems(conn, report, doc_id, problems)


class _RuleNames:
    """公報上的規則名稱 → 刊登它的各件。

    同一件裡的改名（「名稱並修正為」）把新舊名稱連成一條規則。
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._documents: dict[str, set[int]] = {}

    def add(self, doc_id: int, names: Sequence[str], renames: Sequence[tuple[str, str]]) -> None:
        """登記 `doc_id` 刊登了哪些規則名稱，並把其中的改名（舊名，新名）併成同一條規則。"""
        for name in names:
            self._documents.setdefault(self._root(name), set()).add(doc_id)
        for old, new in renames:
            self._union(old, new)

    def documents(self, name: str) -> set[int]:
        """刊登過這條規則（含其舊名與新名）的各件；沒有則為空集合。"""
        return set(self._documents.get(self._root(name), set()))

    def _root(self, name: str) -> str:
        self._parent.setdefault(name, name)
        while self._parent[name] != name:
            name = self._parent[name]
        return name

    def _union(self, a: str, b: str) -> None:
        ra, rb = self._root(a), self._root(b)
        if ra != rb:
            self._parent[rb] = ra
            self._documents.setdefault(ra, set()).update(self._documents.pop(rb, set()))


class _LoadedRow(NamedTuple):
    issuer: str
    document_no: str
    issued_on: date
    kind: str
    subject: str
    body: str
    published_in: str | None
    source_url: str | None


def _loaded(conn: Conn, document_id: int) -> dict[str, Any]:
    """已載入的一件公文，照原樣：重新載入時是同一件，只多停止適用。"""
    row = fetch_one(
        conn,
        "SELECT issuer, document_no, issued_on, rule_kind::text, subject, body, published_in,"
        "       source_url FROM guidance.document WHERE id = %s",
        (document_id,),
        _LoadedRow,
    )
    conn.rollback()
    assert row is not None
    return {
        "issuer": row.issuer,
        "document_no": row.document_no,
        "issued_on": row.issued_on.isoformat(),
        "kind": row.kind,
        "subject": row.subject,
        "body": row.body,
    }


def _ref(conn: Conn, document_id: int) -> dict[str, str]:
    d = _loaded(conn, document_id)
    return {"issuer": d["issuer"], "document_no": d["document_no"]}


def _reload(conn: Conn, document: Mapping[str, Any]) -> int:
    """以 `guidance.load` 對已載入的公文補停止適用；回傳寫入的筆數，被拒為 -1。"""
    try:
        loaded = guidance.load(
            conn,
            {
                "derivation": DERIVATION,
                "documents": [document],
            },
        )
    except guidance.GuidanceError as e:
        log.warning("停止適用未寫入：%s", e)
        return -1
    return loaded.cessations


def _repeal_problems(conn: Conn, report: Report, document_id: int, problems: list[Problem]) -> None:
    """廢止的對應問題記在該件公報刊登的來源項目上；每次重算，所以先清掉上一次的。"""
    with conn.transaction():
        row = fetch_one(
            conn,
            "SELECT i.id, i.last_snapshot_id FROM guidance.document AS d"
            "  JOIN source.item AS i ON i.feed = 'gazette'"
            "   AND i.key = substring(d.source_url FROM 'metaid=([^&]+)')"
            " WHERE d.id = %s",
            (document_id,),
            ItemRow,
        )
        if row is None:
            return
        conn.execute(
            "DELETE FROM source.import_problem WHERE item_id = %s AND problem_code = ANY(%s)",
            (row.id, list(REPEAL_PROBLEMS)),
        )
        record_problems(conn, report, row.id, row.last_snapshot_id, problems)
