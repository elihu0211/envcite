"""Embed the passages of a recorded scope (see golden_scope.py) and nothing else.

    ONTOLOGY_MODELS=models.toml ONTOLOGY_DSN=… \
        uv run python tools/embed_scope.py eval/golden-scope-<date>.json

Uses `projection.embed_scope`, the same batching and per-batch commit as the service's own
projection; a failed batch is retried here with back-off and a rerun continues where it stopped.
Progress goes to the log; the final line reports the scope's embedding completion.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from golden_retrieval_eval import load_scope

from envcite_ontology import embedding, projection
from envcite_ontology.settings import DatabaseSettings, ModelsSettings, load

log = logging.getLogger("embed_scope")

COMPLETION = (
    "SELECT count(DISTINCT v.content_sha256),"
    "       count(DISTINCT v.content_sha256)"
    "         FILTER (WHERE i.embedding IS NOT NULL AND i.model = %s)"
    "  FROM corpus.provision_version AS v"
    "  JOIN corpus.legal_resource AS r ON r.id = v.resource_id"
    "  LEFT JOIN retrieval.passage AS i ON i.content_sha256 = v.content_sha256"
    " WHERE v.superseded_at IS NULL AND v.content IS NOT NULL AND r.local_id = ANY(%s)"
)


def completion(
    conn: psycopg.Connection[tuple[object, ...]], model: str, pcodes: list[str]
) -> tuple[int, int]:
    row = conn.execute(COMPLETION, (model, pcodes)).fetchone()
    assert row is not None
    return int(row[1]), int(row[0])  # type: ignore[call-overload]


def main() -> None:
    parser = argparse.ArgumentParser(prog="embed_scope")
    parser.add_argument("scope", type=Path)
    parser.add_argument("--attempts", type=int, default=6)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)

    pcodes = sorted(e["pcode"] for e in load_scope(args.scope)["laws"])
    embedder = embedding.from_settings(load(ModelsSettings), timeout=120.0, max_retries=3)
    if embedder is None:
        raise SystemExit("沒有設定嵌入端點（ONTOLOGY_MODELS 的 [embedding]）")
    started = time.monotonic()
    with psycopg.connect(load(DatabaseSettings).ontology_dsn) as conn:
        done, total = completion(conn, embedder.model, pcodes)
        log.info(
            "範圍 %d 部法規；段落 %d，已嵌入 %d（模型 %s）",
            len(pcodes),
            total,
            done,
            embedder.model,
        )
        for attempt in range(1, args.attempts + 1):
            try:
                projection.embed_scope(conn, embedder, pcodes)
                break
            except embedding.EmbeddingError as e:
                conn.rollback()
                log.warning("第 %d 次中斷：%s", attempt, e)
                if attempt == args.attempts:
                    raise SystemExit(1) from e
                time.sleep(min(60, 5 * attempt))
        done, total = completion(conn, embedder.model, pcodes)
    log.info("完成：%d/%d 段已嵌入，耗時 %.0f 秒", done, total, time.monotonic() - started)


if __name__ == "__main__":
    main()
