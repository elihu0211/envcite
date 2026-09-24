# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `services/platform/`。

## Project

- **Name:** platform（`services/platform`；倉庫為 `env-law`）
- **Role:** 多租戶 SaaS 平台（control plane）：tenants, accounts, subscriptions/billing, API keys, quotas, public API gateway. Calls `services/ontology` (Python, shared non-tenant data) over gRPC.
- **Stack:** C# / .NET 10 (SDK pinned in `global.json`) · ASP.NET Core · PostgreSQL · xUnit
- **Auth:** API keys only for now (OIDC later for an admin UI)
- **Billing:** not integrated yet; plans assigned manually. Note: Stripe does not onboard Taiwan entities — evaluate Paddle / local gateways when needed.
- **Status:** tenants + API keys, per-tenant rate limiting, usage metering, REST facade over the ontology gRPC service

## Architecture

- Modular monolith: one solution, one deployable; modules as projects (`Modules.Tenancy`, `Modules.Billing`, `Modules.ApiAccess`, …).
- Multi-tenancy is runtime isolation (tenant resolution + `TenantId` query filters), never per-tenant code or repos.
- Billing provider is the source of truth for payment; this service keeps an entitlements table updated via webhooks.

## Commands

Run from **this directory** (`services/platform/`, where `EnvLaw.Platform.slnx` lives).

```bash
dotnet restore && dotnet tool restore  # tool: dotnet-ef (dotnet-tools.json)
dotnet build                         # warnings are errors (Directory.Build.props)
dotnet test                          # needs Docker (Testcontainers PostgreSQL)
dotnet format                        # apply .editorconfig
dotnet format --verify-no-changes    # CI check
dotnet run --project src/EnvLaw.Platform.Api   # Development: local PostgreSQL, auto-migrates

# Migrations: one DbContext per module, each in its own schema
dotnet ef migrations add <Name> -p src/EnvLaw.Platform.Modules.<Module> -s src/EnvLaw.Platform.Api -c <Module>DbContext -o Migrations
```

Ontology contract: `Modules.Ontology` compiles the repo-root `proto/env_law/ontology/v1/ontology.proto` (Grpc.Tools; see its `.csproj`). Never copy the `.proto`.

Config (`Ontology` section): `Address` (e.g. `http://ontology:50051`), `InternalToken` (must equal the service's `ONTOLOGY_INTERNAL_TOKEN`; secret), `Timeout`. Development uses `http://localhost:50051` + token `dev` (`ONTOLOGY_INTERNAL_TOKEN=dev uv run env-law-ontology` in `services/ontology/`).

Config (`Platform` section): `AdminKeySha256` (hex SHA-256 of the operator key; set via user-secrets/env, never commit), `RateLimit:PermitLimit`/`Window`, `ApplyMigrationsOnStartup` (Development/tests only). Connection string: `ConnectionStrings:Platform`.

- Package versions live only in `Directory.Packages.props` (central package management); `dotnet add package` updates it.
- Shared build settings: `Directory.Build.props`. Don't repeat them in `.csproj` files.

## Layout

- `src/EnvLaw.Platform.Api` — ASP.NET Core host (composition root)
- `src/EnvLaw.Platform.Modules.*` — feature modules (`Tenancy`, `ApiAccess`, `Metering`, `Ontology`); reference only `SharedKernel`, never each other directly (cross-module contracts like `IUsageRecorder` live in `SharedKernel`)
- `src/EnvLaw.Platform.SharedKernel` — cross-module primitives
- `tests/EnvLaw.Platform.Tests` — xUnit + `WebApplicationFactory` integration tests (`PlatformFactory`: Testcontainers PostgreSQL + `FakeTimeProvider` + `FakeOntologyServer`, an in-process h2c gRPC fake)

## Auth & tenancy flow

- Tenant API: `Authorization: Bearer sk_live_{prefix}_{secret}` → `ApiKeyAuthenticationHandler` (lookup by prefix, SHA-256 + constant-time compare) → `tenant` claim → Finbuckle resolves tenant → tenant-owned entities (`IsMultiTenant()`) are filtered automatically.
- Operator API (`/admin`): `X-Admin-Key` header, compared against `Platform:AdminKeySha256`.
- Middleware order is load-bearing: `UseAuthentication` → `UseMultiTenant` → `UseAuthorization` → `UseRateLimiter`.
- `/v1/laws/…` calls env-law-ontology over gRPC with `authorization: Bearer <InternalToken>`, `x-tenant-id`, `x-request-id`; gRPC `NotFound` → 404, `Unavailable`/`DeadlineExceeded` → 503. Only successful calls are metered (`IUsageRecorder`, one row per call in `metering.UsageRecords`).
- A `MultiTenantDbContext` captures the tenant at construction: never resolve it before tenant resolution; for a specific tenant outside a tenant request use `MultiTenantDbContext.Create<TContext, TenantInfo>(tenant, services)`.

## Conventions

- Formatting rules: `.editorconfig`.

## Safety

- Never commit secrets, connection strings, billing keys, or real `.env*` / local appsettings values.
- Every tenant-scoped query must be filtered by `TenantId`; never disable query filters without review.
- Bypass only the tenant filter by name (`IgnoreQueryFilters([TenantToken])`), and only in reviewed cross-tenant lookups (currently: API key authentication).
- Generated: `src/*/Migrations/` (change via `dotnet ef`, never by hand; marked `generated_code` in `.editorconfig`), gRPC code under `obj/` (from the repo-root proto).
- Also ask before applying migrations to shared DBs (general ask-before list: root `AGENTS.md`).

## Done when

- `dotnet build` and `dotnet test` pass; `dotnet format --verify-no-changes` is clean — enforced by `.gitlab-ci.yml` (Docker-in-Docker for Testcontainers).
