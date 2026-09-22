# AGENTS.md

Vendor-neutral instructions for coding agents (Cursor, Codex, Claude Code, Copilot, Gemini CLI, …).

This file is canonical. `CLAUDE.md` only imports it (`@AGENTS.md`) per [Claude Code docs](https://code.claude.com/docs/en/memory) so sessions that can't read `AGENTS.md` directly still get it. Never duplicate this content into vendor-specific files.

## Project

- **Name:** env-law-platform
- **Role:** 多租戶 SaaS 平台（control plane）：tenants, accounts, subscriptions/billing, API keys, quotas, public API gateway. Calls `env-law-ontology` (Python, shared non-tenant data) via its OpenAPI contract.
- **Stack:** C# / .NET 10 (SDK pinned in `global.json`) · ASP.NET Core · PostgreSQL · xUnit
- **Auth:** API keys only for now (OIDC later for an admin UI)
- **Billing:** not integrated yet; plans assigned manually. Note: Stripe does not onboard Taiwan entities — evaluate Paddle / local gateways when needed.
- **Status:** solution scaffolded (`/health` only), no domain code yet
- **Remote:** `git@gitlab.com:thuaisse-lab/env-law-platform.git`

Keep this file short. Long procedures → `.agents/skills/<name>/SKILL.md`.

## Architecture

- Modular monolith: one solution, one deployable; modules as projects (`Modules.Tenancy`, `Modules.Billing`, `Modules.ApiAccess`, …).
- Multi-tenancy is runtime isolation (tenant resolution + `TenantId` query filters), never per-tenant code or repos.
- Billing provider is the source of truth for payment; this service keeps an entitlements table updated via webhooks.

## Layering (canonical)

| Layer | Path | Loaded when |
|-------|------|-------------|
| Baseline | `AGENTS.md` (this file) | Always (all agents) |
| Skills | `.agents/skills/<name>/SKILL.md` | On demand ([agentskills.io](https://agentskills.io/specification)) |
| Claude bridge | `CLAUDE.md` → `@AGENTS.md`; `.claude/skills` → symlink to `.agents/skills` | Claude Code only |
| Cursor rules | `.cursor/rules/*.mdc` | Cursor only (globs / intelligent / manual) |
| Gemini bridge | `.gemini/settings.json` → `context.fileName: ["AGENTS.md"]` | Gemini CLI only (default is `GEMINI.md`) |

- Author skills in **`.agents/skills/`** only; `.claude/skills` is a symlink, never a separate tree.
- Prefer **`AGENTS.md`** for always-on ops; use `.cursor/rules` only for Cursor path scoping.
- Nested `AGENTS.md` in a subdirectory wins for that tree.

## Commands

Run from repo root (`EnvLaw.Platform.slnx`).

```bash
dotnet restore
dotnet build                         # warnings are errors (Directory.Build.props)
dotnet test
dotnet format                        # apply .editorconfig
dotnet format --verify-no-changes    # CI check
dotnet run --project src/EnvLaw.Platform.Api
```

- Package versions live only in `Directory.Packages.props` (central package management); `dotnet add package` updates it.
- Shared build settings: `Directory.Build.props`. Don't repeat them in `.csproj` files.

## Layout

- `src/EnvLaw.Platform.Api` — ASP.NET Core host (composition root)
- `src/EnvLaw.Platform.Modules.*` — feature modules (`Tenancy`, `ApiAccess`); reference only `SharedKernel`, never each other directly
- `src/EnvLaw.Platform.SharedKernel` — cross-module primitives
- `tests/EnvLaw.Platform.Tests` — xUnit + `WebApplicationFactory` integration tests

## Conventions

- Small, focused diffs; match existing style. Formatting rules: `.editorconfig`.
- Human docs → `README.md`; agent ops → this file + skills.
- Do not add `.cursorrules`, `GEMINI.md`, or other duplicate baselines; `CLAUDE.md` stays an import-only bridge.

## Safety

- Never commit secrets, connection strings, billing keys, or real `.env*` / local appsettings values.
- Every tenant-scoped query must be filtered by `TenantId`; never disable query filters without review.
- Ask before: force-push, history rewrite, production deploys, destructive DB ops, applying migrations to shared DBs.

## Done when

- `dotnet build` and `dotnet test` pass; `dotnet format --verify-no-changes` is clean.
- Commands and Safety lists stay accurate after tooling changes.

## Skills in this repo

| Skill | Use when |
|-------|----------|
| `maintain-agent-context` | Editing agent instruction / skill / rule layout |
| `bootstrap-stack` | Creating the solution and wiring Commands |
