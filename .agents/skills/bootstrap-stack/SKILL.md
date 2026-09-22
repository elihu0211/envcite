---
name: bootstrap-stack
description: >-
  Choose or wire the .NET stack for env-law-platform and fill AGENTS.md Commands.
  Use when creating the solution/projects, adding NuGet packages, picking
  multi-tenancy/billing libraries, or replacing placeholder build/test/format commands.
---

# Bootstrap stack

## Goal

Turn the empty `env-law-platform` repo into a runnable ASP.NET Core modular monolith and keep agent ops accurate.

## Steps

1. Confirm open decisions with the user (billing provider, tenancy library, database, auth provider). Check official docs before wiring any library.
2. Scaffold only what was agreed; follow the module layout in `AGENTS.md` → Architecture.
3. Update **`AGENTS.md` → Commands** with exact, copy-pasteable `dotnet` commands.
4. List generated paths (e.g. EF Core migrations, generated API clients) under **Safety**.
5. The `env-law-ontology` API client is generated from its OpenAPI spec — never hand-write its DTOs.
6. Add a skill under `.agents/skills/` only if a workflow is longer than a short AGENTS.md section.

## Do not

- Split modules into separate repos or deployables without an explicit decision
- Copy `AGENTS.md` content into `CLAUDE.md` or other vendor files
- Leave placeholder commands after the stack is real
