"""Extraction-only A/B: run `run()` with no verifiers on a slice, once per arm and repeat.

    python tools/ab_extract.py <dir with slices and models_A.toml, models_B.toml> <pcode>

Arm B is models_A.toml plus `constrained_extraction = true` on the extractor. Needs a postgres
(pgvector) on localhost:55432 and CHAT_API_KEY, CHAT_BASE_URL in the environment. Counts go to
<pcode>_extract_only.jsonl. Identical requests may be served from an endpoint cache, so a repeat is
not necessarily an independent run.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import psycopg

from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import audit, importer
from envcite_ontology.migrate import apply_all
from envcite_ontology.verification import config
from envcite_ontology.verification.run import run
from tests.sample import stored_snapshot

S = Path(sys.argv[1])
pc = sys.argv[2]
audit.LIMITS["unconfirmed_articles"] = (audit.LIMITS["unconfirmed_articles"][0], 0.5)
ADMIN = "postgresql://u:pw@localhost:55432/postgres"
law = json.loads((S / f"{pc}.json").read_text(encoding="utf-8"))
out = S / f"{pc}_extract_only.jsonl"
for arm in "AB":
    for r in (1, 2):
        db = f"eo_{pc.lower()}_{arm.lower()}{r}"
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{db}"')
            c.execute(f'CREATE DATABASE "{db}"')
        with psycopg.connect(f"postgresql://u:pw@localhost:55432/{db}") as conn:
            apply_all(conn)
            with tempfile.TemporaryDirectory() as t:
                stored_snapshot(conn, Path(t), [law])
                importer.run(conn, Path(t), Moj())
            conn.commit()
            models = config.load(S / f"models_{arm}.toml", os.environ)
            t0 = time.time()
            res = run(
                conn,
                models.extractor,
                [],
                [pc],
                agent_version=f"eo-{arm}{r}",
                check_distinct=False,
                gates=[],
                definition_gates_=[],
            )
            rec = dict(
                law=pc,
                arm=arm,
                run=r,
                units=res.units,
                extracted=res.extracted,
                dropped=res.dropped,
                definitions=res.definitions,
                calls=res.calls,
                repairs=res.repairs,
                syntax_failures=res.syntax_failures,
                shape_failures=res.shape_failures,
                tokens=getattr(models.extractor, "tokens", None),
                seconds=round(time.time() - t0),
            )
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(rec, flush=True)
