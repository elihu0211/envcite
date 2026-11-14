"""抽取紀錄與審查後的重抽。"""

import logging
from collections.abc import Sequence
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch_one
from envcite_ontology.verification.definitions import (
    ExtractedDefinitions,
    defined_terms,
)
from envcite_ontology.verification.extract import Extracted, extract_unit
from envcite_ontology.verification.link import penalized
from envcite_ontology.verification.models import Model
from envcite_ontology.verification.reextract import due_units, review_notes

log = logging.getLogger(__name__)


#: 沒有答案可記的結果：模型沒回或答不成 JSON，下次再試。
_NO_ANSWER = ("<model error", "<unparseable model output>")


def record_attempt(
    conn: Conn,
    law: str,
    path: str,
    agent: str,
    version: str,
    out: Extracted | ExtractedDefinitions,
    round: int = 0,
) -> None:
    """記一筆抽取紀錄（沒有答案可記的結果不記），一個交易；已有同一筆則不動。"""
    if any(reason.startswith(_NO_ANSWER) for reason in out.dropped):
        return
    conn.execute(
        "INSERT INTO annotation.extraction_attempt"
        "  (provision_id, content_sha256, producer, producer_version,"
        "   statement_count, dropped_count, round)"
        " SELECT p.id, v.content_sha256, %s, %s, %s, %s, %s"
        "   FROM corpus.provision AS p"
        "   JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "   JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        "  WHERE r.local_id = %s AND p.path = %s"
        " ON CONFLICT DO NOTHING",
        (agent, version, _count(out), len(out.dropped), round, law, path),
    )
    conn.commit()


def _count(out: Extracted | ExtractedDefinitions) -> int:
    if out.loaded is None:
        return 0
    return out.loaded.statements if isinstance(out, Extracted) else out.loaded.definitions


class _ProvisionRow(NamedTuple):
    id: int
    content_sha256: bytes


_PROVISION = (
    "SELECT p.id, v.content_sha256 FROM corpus.provision AS p"
    "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
    "  JOIN corpus.provision_version AS v ON v.provision_id = p.id"
    "   AND v.superseded_at IS NULL"
    " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s"
)


def reextract(
    conn: Conn, extractor: Model, laws: Sequence[str], agent_version: str, max_round: int
) -> tuple[int, int]:
    """義務沒有定論的單元各再抽一輪（帶審查意見）；回傳（重抽的單元數，寫入的陳述數）。

    `annotation.reextraction_due` 決定單元與各自的輪號（中斷後續跑，各單元停在不同輪也各自接著做）；
    只抽輪號不超過 `max_round` 的。輪數上限在資料庫（`extraction_attempt.round`）。
    """
    units = statements = 0
    for law in laws:
        todo = [
            d for d in due_units(conn, extractor.name, agent_version, law) if d.round <= max_round
        ]
        if not todo:
            continue
        terms = defined_terms(conn, law)
        punished = penalized(conn, law)
        for due in todo:
            provision = fetch_one(conn, _PROVISION, (law, due.path), _ProvisionRow)
            assert provision is not None
            out = extract_unit(
                conn,
                extractor,
                law,
                due.path,
                agent_version=agent_version,
                defined=terms,
                punished_by=punished.get(due.path, ()),
                review=review_notes(conn, provision.id, provision.content_sha256),
            )
            record_attempt(conn, law, due.path, extractor.name, agent_version, out, due.round)
            units += 1
            statements += out.loaded.statements if out.loaded else 0
            for reason in out.dropped:
                log.warning("重抽第 %d 輪丟掉 %s %s：%s", due.round, law, due.path, reason[:300])
    return units, statements
