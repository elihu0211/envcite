# AGENTS.md

Vendor-neutral instructions for coding agents (Cursor, Codex, Claude Code, Copilot, Gemini CLI, …).

This file is canonical. `CLAUDE.md` only imports it (`@AGENTS.md`) per [Claude Code docs](https://code.claude.com/docs/en/memory) so sessions that can't read `AGENTS.md` directly still get it. Never duplicate this content into vendor-specific files.

## Project

- **Name:** env-law-ontology (Python package `env_law`)
- **Role:** 環境法規本體論服務（shared, non-tenant data）；由同倉的 `services/platform`（C# SaaS：租戶、計費、API 金鑰）呼叫
- **Stack:** Python 3.14 · uv · gRPC（grpcio，同步伺服器）· PostgreSQL（psycopg 3）· pySHACL · ruff · mypy (strict) · pytest
- **Data model:** [ELI ontology](http://data.europa.eu/eli/ontology)，以**關聯式實表**承載（`legal_resource` / `legal_expression` / `subdivision`）。標題掛在 `legal_expression`——`eli:title` 的定義域是表達層，不是資源層。RDF 是由實表投影的導出視圖，方向不可顛倒；論據見 `env-law-docs` 的 01-01 與 02-02。
- **Status:** gRPC `OntologyService`（GetLaw、ListArticles）讀 PostgreSQL；資料仍為**樣本 fixture**（`migrations/0002_sample_fixture.sql`），全國法規資料庫的匯入管線尚未建立
- **Remote:** `git@gitlab.com:thuaisse-lab/env-law-ontology.git`

Keep this file short. Long procedures → `.agents/skills/<name>/SKILL.md`.

## Layering (canonical)

| Layer | Path | Loaded when |
|-------|------|-------------|
| Baseline | `AGENTS.md` (this file) | Always (all agents) |
| Skills | `.agents/skills/<name>/SKILL.md` | On demand ([agentskills.io](https://agentskills.io/specification)) |
| Shared skills | installed from [`elihu0211/agent-config`](https://github.com/elihu0211/agent-config) (public GitHub), pinned in `skills-lock.json` | Same as Skills; update with `npx skills update -p -y` |
| Claude bridge | `CLAUDE.md` → `@AGENTS.md`; `.claude/skills` → symlink to `.agents/skills` | Claude Code only |
| Cursor rules | `.cursor/rules/*.mdc` (none yet) | Cursor only, for path-scoped guidance |
| Gemini bridge | `.gemini/settings.json` → `context.fileName: ["AGENTS.md"]` | Gemini CLI only (default is `GEMINI.md`) |

- Author repo-specific skills in **`.agents/skills/`**; `.claude/skills` is a symlink, never a separate tree.
- Never hand-edit skills listed in `skills-lock.json`; change them in `agent-config`, then `DISABLE_TELEMETRY=1 npx skills update -p -y`.
- Prefer **`AGENTS.md`** for always-on ops; use `.cursor/rules` only for Cursor path scoping.
- Nested `AGENTS.md` in a subdirectory wins for that tree.

## Commands

Use `uv` only (no pip/venv/poetry). Run from **this directory** (`services/ontology/`).

```bash
uv sync                      # install (creates .venv from uv.lock)
uv add <pkg>                 # runtime dep; uv add --dev <pkg> for dev-only
uv run python -m pytest       # test
uv run ruff check .           # lint (add --fix to autofix)
uv run ruff format .          # format
uv run python -m mypy src tests   # typecheck (strict)
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 lint        # proto lint
DISABLE_TELEMETRY=1 npx @bufbuild/buf@1.73.0 breaking --against '.git#branch=main'   # before changing protos
./scripts/generate-proto.sh   # regenerate src/env_law/ontology/v1/* after editing protos
ONTOLOGY_TEST_DSN=postgresql://... uv run python -m pytest   # use an existing database
ONTOLOGY_INTERNAL_TOKEN=dev ONTOLOGY_DSN=postgresql://... uv run env-law-ontology   # run server (:50051)
```

- Config lives in `pyproject.toml`. Code in `src/env_law/`, tests in `tests/`.
- Server env：`ONTOLOGY_INTERNAL_TOKEN`（必填；呼叫端送 `authorization: Bearer <token>`）、`ONTOLOGY_DSN`（必填，PostgreSQL 連線字串）、`ONTOLOGY_GRPC_PORT`（50051）。
- 測試需要**真實的 PostgreSQL**：設 `ONTOLOGY_TEST_DSN` 指向現成的資料庫，或留空讓 testcontainers 起一個（需要 Docker daemon）。不用替身，因為本服務一半的讀取邏輯在 SQL 裡（整數陣列排序、衍生欄位、CHECK 約束），對替身測試等於不測那一半。
- 綱要由 `migrations/*.sql` 描述，依檔名順序套用。**已套用的檔案不可修改**——要改就新增一個檔，這條由 `schema_migration` 的雜湊比對強制，不靠記得。

## Contract

- 倉庫根的 `proto/env_law/ontology/v1/ontology.proto` 是 gRPC API 的**單一真值**；`services/platform` 由同一份產生程式碼，**不得各自複製**。（子模組共享已於三倉合一時移除。）
- Breaking changes need a new package version (`v2`); `buf breaking` must pass for `v1`.
- Data is not tenant-scoped; `x-tenant-id` / `x-request-id` metadata are for logging/audit only.
- `src/env_law/data/shapes.ttl`（SHACL）是 ELI 一致性規格，**目前不套用在任何線上資料上**——本體投影尚未建立。它以負向測試守住不腐壞，投影落地時才接上。

## Conventions

- Small, focused diffs; match existing style when code appears.
- Human docs → `README.md`; agent ops → this file + skills.
- Do not add `.cursorrules`, `GEMINI.md`, or other duplicate baselines; `CLAUDE.md` stays an import-only bridge.

## Safety

- Never commit secrets or real `.env*` values.
- Ask before: force-push, history rewrite, production deploys, destructive DB ops.
- Do not hand-edit generated paths: `uv.lock` (change via `uv add/remove/lock`), `.venv/`, `src/env_law/ontology/v1/*_pb2*` (run `scripts/generate-proto.sh`).
- Sample data (`src/env_law/data/laws.ttl`) is a placeholder fixture, not authoritative law text; real data must cite 全國法規資料庫 (政府資料開放授權條款－第1版, attribution required).

## Done when

- ruff, mypy, pytest and `buf lint` pass — enforced by `.gitlab-ci.yml` (`python` job also fails if generated gRPC code is stale; `proto` job runs `buf breaking` against the MR target branch).
- Commands and Safety lists stay accurate after tooling changes.

## Skills in this repo

| Skill | Source | Use when |
|-------|--------|----------|
| `maintain-agent-context` | `agent-config` | Editing agent instruction / skill / rule layout |
| `bootstrap-stack` | this repo | Choosing stack and wiring Commands |

