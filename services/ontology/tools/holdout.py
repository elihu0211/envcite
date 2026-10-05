"""Hold-out evaluation of `env-law-pipeline` on a law it has not been tuned on.

    uv run python tools/holdout.py O0030001 noise-a 55481 --log noise-a.txt

Slice rule, fixed before any outcome is looked at: the first six articles that fix a fine
(處新臺幣…罰鍰) plus the articles they cite that the corpus has. Articles the importer cannot
re-split are left out (the stored text has lost its indentation). The slice is rebuilt from the
development database (read-only) into a throwaway postgres container, loaded through the real
importer, and the pipeline runs on it with `models.toml`. Run it twice for run-to-run stability.

The model endpoint and key come from the environment variables `models.toml` names
(`CHAT_BASE_URL`, `CHAT_API_KEY` in the example); this script never reads or writes a key file.
A slice the importer rejects stops here, before any model is called. A law that has been looked at
is spent: report first-look counts only, never accuracy, and pick another for the next measurement.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from env_law_ontology.acquire.moj import Moj
from env_law_ontology.ingest import articles as art_mod
from env_law_ontology.ingest import importer
from env_law_ontology.migrate import apply_all
from env_law_ontology.verification.gates import citations
from env_law_ontology.verification.run import units_of
from tests.sample import stored_snapshot

DEV_DSN = os.environ.get("ONTOLOGY_DEV_DSN", "postgresql://env_law:env_law@localhost:5432/env_law")
FINE = re.compile(r"處新臺幣.{0,40}罰鍰")


def _articles(c: psycopg.Connection[Any], rid: int) -> dict[str, str]:
    """Article text as the MOJ export has it: the article's own line, or its paragraphs and
    subparagraphs, one per line."""
    out: dict[str, list[str]] = {}
    for path, content in c.execute(
        "SELECT p.path, v.content FROM corpus.provision p JOIN corpus.provision_version v"
        " ON v.provision_id = p.id AND v.superseded_at IS NULL"
        " WHERE p.resource_id = %s AND v.content IS NOT NULL ORDER BY p.sort_key, p.path",
        (rid,),
    ):
        out.setdefault(str(path).split("/")[0], []).append(str(content))
    return {a: "\r\n".join(lines) for a, lines in out.items()}


def select(pcode: str) -> dict[str, Any]:
    with psycopg.connect(DEV_DSN, connect_timeout=5) as c:
        c.read_only = True
        row = c.execute(
            "SELECT id FROM corpus.legal_resource WHERE id_scheme = 'moj_pcode' AND local_id = %s",
            (pcode,),
        ).fetchone()
        if row is None:
            sys.exit(f"{pcode} is not in the development database")
        rid = int(str(row[0]))
        title = str(
            c.execute(
                "SELECT title FROM corpus.legal_expression WHERE resource_id = %s"
                " ORDER BY version_date DESC LIMIT 1",
                (rid,),
            ).fetchone()[0]  # type: ignore[index]
        )
        events = [
            str(r[0])
            for r in c.execute(
                "SELECT source_text FROM corpus.lifecycle_event"
                " WHERE resource_id = %s AND source_text IS NOT NULL ORDER BY event_date, id",
                (rid,),
            )
        ]
        arts = _articles(c, rid)
    fined = [a for a, t in arts.items() if FINE.search(t)][:6]
    cited = {re.sub(r"/.*", "", x) for a in fined for x in citations(arts[a])}
    picked = [a for a in arts if a in set(fined) | cited]
    left_out = [a for a in picked if art_mod.split(a, arts[a]).degraded]
    picked = [a for a in picked if a not in left_out]
    print(
        f"{pcode} {title}: {len(arts)} articles, fined {fined}, picked {len(picked)}, "
        f"left out {left_out}"
    )
    return {
        "LawLevel": "法律",
        "LawName": title,
        "LawURL": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}",
        "LawModifiedDate": "",
        "LawEffectiveDate": "",
        "LawEffectiveNote": "",
        "LawAbandonNote": "",
        "LawHistories": "\r\n".join(events),
        "LawArticles": [
            {"ArticleType": "A", "ArticleNo": f"第 {a[4:]} 條", "ArticleContent": arts[a]}
            for a in picked
        ],
    }


def start_database(name: str, port: int) -> str:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=pw",
            "-e",
            "POSTGRES_USER=u",
            "-e",
            "POSTGRES_DB=d",
            "-p",
            f"{port}:5432",
            "postgres:18",
        ],
        check=True,
        capture_output=True,
    )
    for _ in range(60):
        if (
            subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "u", "-d", "d"], capture_output=True
            ).returncode
            == 0
        ):
            time.sleep(2)
            break
        time.sleep(1)
    return f"postgresql://u:pw@localhost:{port}/d"


def load(dsn: str, law: dict[str, Any], pcode: str) -> int:
    with psycopg.connect(dsn) as conn:
        apply_all(conn)
        with tempfile.TemporaryDirectory() as t:
            stored_snapshot(conn, Path(t), [law])
            try:
                importer.run(conn, Path(t), Moj())
            except importer.BatchRejected as e:
                sys.exit(f"the importer rejected the slice, nothing was run: {e}")
        conn.commit()
        return len(units_of(conn, pcode))  # type: ignore[arg-type]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("pcode")
    ap.add_argument("name", help="container name")
    ap.add_argument("port", type=int)
    ap.add_argument("--agent-version", default="1")
    ap.add_argument("--log", type=Path, required=True, help="where the pipeline log goes")
    ap.add_argument("--slice", type=Path, help="write the chosen slice here (JSON)")
    ap.add_argument("--models", default="models.toml")
    args = ap.parse_args()

    law = select(args.pcode)
    if args.slice:
        args.slice.write_text(json.dumps(law, ensure_ascii=False), encoding="utf-8")
    dsn = start_database(args.name, args.port)
    units = load(dsn, law, args.pcode)
    if units == 0:
        sys.exit("the slice loaded no units, nothing was run")
    print(f"{units} units loaded; running the pipeline (log: {args.log})")
    env = {**os.environ, "ONTOLOGY_DSN": dsn}
    with args.log.open("w", encoding="utf-8") as log:
        code = subprocess.run(
            [
                "uv",
                "run",
                "env-law-pipeline",
                "--law",
                args.pcode,
                "--models",
                args.models,
                "--agent-version",
                args.agent_version,
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        ).returncode
        log.write(f"exit={code}\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
