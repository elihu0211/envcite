# AGENTS.md

Vendor-neutral instructions for coding agents (Cursor, Codex, Claude Code, Copilot, Gemini CLI, …).

This file is canonical. `CLAUDE.md` only imports it (`@AGENTS.md`) per [Claude Code docs](https://code.claude.com/docs/en/memory) so sessions that can't read `AGENTS.md` directly still get it. Never duplicate this content into vendor-specific files.

## Project

- **Name:** env-law-ontology (Python package `env_law`)
- **Role:** 環境法規本體論服務（shared, non-tenant data）；由同倉的 `services/platform`（C# SaaS：租戶、計費、API 金鑰）呼叫
- **Stack:** Python 3.14 · uv · gRPC (grpcio, sync server) · pyoxigraph (RDF store + SPARQL) · pySHACL · ruff · mypy (strict) · pytest
- **Data model:** [ELI ontology](http://data.europa.eu/eli/ontology) (RDF/OWL). Titles live on `eli:LegalExpression` (domain of `eli:title`), not on the `LegalResource`.
- **Status:** gRPC `OntologyService` (GetLaw, ListArticles) over a **sample** fixture; real data import from 全國法規資料庫 not built yet
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

Use `uv` only (no pip/venv/poetry). Run from repo root.

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
ONTOLOGY_INTERNAL_TOKEN=dev uv run env-law-ontology      # run server (:50051)
```

- Config lives in `pyproject.toml`. Code in `src/env_law/`, tests in `tests/`.
- Server env: `ONTOLOGY_INTERNAL_TOKEN` (required; callers send `authorization: Bearer <token>`), `ONTOLOGY_GRPC_PORT` (50051), `ONTOLOGY_DATA` (Turtle file; default: packaged sample).

## Contract

- 倉庫根的 `proto/env_law/ontology/v1/ontology.proto` 是 gRPC API 的**單一真值**；`services/platform` 由同一份產生程式碼，**不得各自複製**。（子模組共享已於三倉合一時移除。）
- Breaking changes need a new package version (`v2`); `buf breaking` must pass for `v1`.
- Data is not tenant-scoped; `x-tenant-id` / `x-request-id` metadata are for logging/audit only.
- Validate ingested RDF against `src/env_law/data/shapes.ttl` (SHACL).

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

