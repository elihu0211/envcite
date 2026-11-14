"""逐項處理：下載、寫入公文，並在一個交易內記快照、來源項目與匯入問題。"""

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from envcite_jobkit import ItemError
from envcite_ontology import guidance
from envcite_ontology.acquire import gazette, moenv
from envcite_ontology.acquire.engine import InvalidSnapshot
from envcite_ontology.clock import Today
from envcite_ontology.db import Conn, scalar
from envcite_ontology.guidance_anchor import Resolver
from envcite_ontology.guidance_sync.client import Client
from envcite_ontology.guidance_sync.problems import REPEAL_PROBLEMS, record_problems
from envcite_ontology.guidance_sync.provenance import DERIVATION
from envcite_ontology.guidance_sync.report import Report
from envcite_ontology.import_problems import Problem


@dataclass(frozen=True)
class Captured:
    """一個工作項下載並寫入公文之後、記入取得紀錄之前的結果。"""

    feed: str
    data_date: date
    license_notice: str
    origin_name: str | None
    #: （來源內的鍵，內容的 SHA-256，匯入問題）
    items: Sequence[tuple[str, bytes, Sequence[Problem]]]


class Job:
    """一次同步的逐項處理：持有連線、報告與錨定解析器，一個工作項一個交易。"""

    def __init__(
        self, conn: Conn, root: Path, report: Report, resolver: Resolver, today: Today
    ) -> None:
        self._conn = conn
        self._root = root
        self._report = report
        self._resolver = resolver
        self._today = today

    def pack(self, client: Client, pack: gazette.Pack) -> None:
        """處理公報的一包：每則行政規則寫入 `guidance`，並記快照與匯入問題。

        包的內容不合（`InvalidSnapshot`）轉成 `ItemError`，由 jobkit 隔離這一包。
        """
        partial = self._partial("gazette")
        try:
            digest, size = client.file(pack.url, partial)
            try:
                data_date, rules = gazette.read(partial)
            except InvalidSnapshot as e:
                raise ItemError(f"{pack.name}: {e}") from e
            items = [(r.meta_id, _digest(r), self._gazette_rule(r)) for r in rules]
            captured = Captured("gazette", data_date, gazette.ATTRIBUTION, pack.name, items)
            self._record(captured, partial, digest, size)
        finally:
            partial.unlink(missing_ok=True)

    def _gazette_rule(self, r: gazette.Rule) -> list[Problem]:
        try:
            kind = gazette.rule_kind(r.title)
        except gazette.DelegatedRegulationTitle:
            return [Problem("warning", "delegated_regulation", r.title)]
        except gazette.AmbiguousKind:
            return [Problem("error", "kind_ambiguous", r.title)]
        if r.issued_on is None or not r.body or not r.document_no or not r.issuer:
            return [Problem("error", "incomplete", r.title)]
        problems: list[Problem] = []
        interprets: list[dict[str, str]] = []
        for cited, citation in self._resolver.basis(r.body):
            found, missing = self._resolver.anchors(cited, r.issued_on, citation)
            interprets += found
            problems += missing
        document = {
            "issuer": r.issuer,
            "document_no": moenv.normalize_document_no(r.document_no),
            "issued_on": r.issued_on.isoformat(),
            "kind": kind,
            "subject": r.subject or r.title,
            "body": r.body,
            "published_in": r.published_in,
            "source_url": r.source_url,
            "interprets": interprets,
        }
        return problems + self._load(document)

    def letter(self, client: Client, row: moenv.Row) -> None:
        """處理函釋清單上的一列：抓詳細頁、寫入 `guidance`，並記快照與匯入問題。

        頁面讀不出來（`UnreadablePage`）轉成 `ItemError`，由 jobkit 隔離這一列。快照存的是
        欄位表的片段（`moenv.content`），不是整頁。
        """
        try:
            fragment = moenv.content(client.text(row.url))
            letter = moenv.parse(fragment)
        except moenv.UnreadablePage as e:
            raise ItemError(f"{row.url}: {e}") from e
        partial = self._partial("moenv_guidance")
        try:
            data = fragment.encode()
            partial.write_bytes(data)
            problems: list[Problem] = []
            interprets: list[dict[str, str]] = []
            for cited in (c for related in letter.related for c in moenv.expand(related)):
                found, missing = self._resolver.anchors(cited, letter.issued_on)
                interprets += found
                problems += missing
            document: dict[str, Any] = {
                "issuer": letter.issuer,
                "document_no": letter.document_no,
                "issued_on": letter.issued_on.isoformat(),
                "kind": "interpretation",
                "subject": letter.subject,
                "body": letter.body,
                "source_url": row.url,
                "interprets": interprets,
            }
            if row.ceased and row.ceased_on is None:
                problems.append(Problem("warning", "ceased_without_date", row.url))
            elif row.ceased_on is not None and row.ceased_on < letter.issued_on:
                problems.append(Problem("warning", "ceased_before_issue", f"{row.ceased_on}"))
            elif row.ceased_on is not None:
                document["ceased_on"] = row.ceased_on.isoformat()
            problems += self._load(document)
            item = (letter.document_no, hashlib.sha256(data).digest(), problems)
            captured = Captured("moenv_guidance", self._today(), moenv.ATTRIBUTION, None, [item])
            self._record(captured, partial, hashlib.sha256(data).digest(), len(data))
        finally:
            partial.unlink(missing_ok=True)

    def _load(self, document: Mapping[str, Any]) -> list[Problem]:
        """一件公文一個交易；內容與已載入者不同不改寫，記成問題（公文不可改）。"""
        try:
            loaded = guidance.load(self._conn, {"derivation": DERIVATION, "documents": [document]})
        except guidance.DocumentChanged as e:
            return [Problem("warning", "content_changed", str(e))]
        except guidance.GuidanceError as e:
            return [Problem("error", "rejected", str(e))]
        self._report.documents += loaded.documents - loaded.repeated
        self._report.repeated += loaded.repeated
        self._report.interpretations += loaded.interpretations
        self._report.cessations += loaded.cessations
        return []

    def _partial(self, feed: str) -> Path:
        partial = self._root / ".partial" / feed
        partial.parent.mkdir(parents=True, exist_ok=True)
        return partial

    def _record(self, c: Captured, partial: Path, digest: bytes, size: int) -> None:
        """快照、來源項目與匯入問題，一個交易。檔案先就位、資料庫後提交，檔名即雜湊。"""
        location = f"{c.feed}/{digest.hex()}"
        (self._root / location).parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, self._root / location)
        conn = self._conn
        with conn.transaction():
            snapshot_id = scalar(
                conn,
                "INSERT INTO source.snapshot"
                " (feed, data_date, sha256, size_bytes, location, license_notice, origin_name)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (feed, sha256) DO UPDATE SET origin_name"
                "   = coalesce(source.snapshot.origin_name, excluded.origin_name)"
                " RETURNING id",
                (c.feed, c.data_date, digest, size, location, c.license_notice, c.origin_name),
                int,
            )
            for key, sha, problems in c.items:
                item_id = scalar(
                    conn,
                    "INSERT INTO source.item"
                    " (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
                    " VALUES (%(feed)s, %(key)s, %(sha)s, %(id)s, %(id)s)"
                    " ON CONFLICT (feed, key) DO UPDATE"
                    "   SET content_sha256 = excluded.content_sha256,"
                    "       last_snapshot_id = excluded.last_snapshot_id"
                    " RETURNING id",
                    {"feed": c.feed, "key": key, "sha": sha, "id": snapshot_id},
                    int,
                )
                conn.execute(
                    "DELETE FROM source.import_problem"
                    " WHERE item_id = %s AND NOT problem_code = ANY(%s)",
                    (item_id, list(REPEAL_PROBLEMS)),
                )
                record_problems(conn, self._report, item_id, snapshot_id, problems)


def _digest(rule: gazette.Rule) -> bytes:
    canonical = json.dumps(asdict(rule), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).digest()
