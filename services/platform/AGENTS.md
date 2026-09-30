# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `services/platform/`。

## Project

- **Name:** platform（`services/platform`；倉庫為 `env-law`）
- **Role:** 多租戶控制平面（control plane）：tenants, API keys, rate limiting, usage metering, public REST API. Calls `services/ontology` (Python, shared non-tenant data) and `services/consult` (grounding evidence, tenant-scoped) over gRPC.
- **Stack:** C# / .NET 10 (SDK pinned in `global.json`) · ASP.NET Core · PostgreSQL · xUnit
- **Auth:** API keys only for now (OIDC later for an admin UI)
- **Billing:** not integrated yet; plans assigned manually. Note: Stripe does not onboard Taiwan entities — evaluate Paddle / local gateways when needed.
- **Status:** tenants + API keys, per-tenant rate limiting, usage metering, REST facades over the ontology and consult gRPC services

## Architecture

- Modular monolith: one solution, one deployable; modules as projects (`Modules.Tenancy`, `Modules.ApiAccess`, `Modules.Metering`, …).
- Two DbContexts, no more: `TenancyDbContext` (Finbuckle requires the EFCore tenant store to derive from `EFCoreStoreDbContext`, so it cannot share) and `PlatformDbContext` in `EnvLaw.Platform.Data` for all tenant-owned data (schema `platform`). A new tenant-owned entity goes into `PlatformDbContext`; modules do not get their own contexts — each extra context is another migration set, bundle and Job step, and no cross-module transaction.
- Multi-tenancy is runtime isolation (tenant resolution + `TenantId` query filters), never per-tenant code or repos.
- When billing is integrated, the provider is the source of truth for payment; this service will keep an entitlements table updated via its webhooks.

## Commands

Run from **this directory** (`services/platform/`, where `EnvLaw.Platform.slnx` lives).

```bash
dotnet restore && dotnet tool restore  # tool: dotnet-ef (dotnet-tools.json)
dotnet build                         # warnings are errors (Directory.Build.props)
dotnet test                          # needs Docker (Testcontainers PostgreSQL)
dotnet format                        # apply .editorconfig
dotnet format --verify-no-changes    # CI check
dotnet run --project src/EnvLaw.Platform.Api   # Development: local PostgreSQL, auto-migrates

# Migrations: each context's project is its own startup project (design-time factory, not the app host)
dotnet ef migrations add <Name> -p src/EnvLaw.Platform.Data -s src/EnvLaw.Platform.Data
dotnet ef migrations add <Name> -p src/EnvLaw.Platform.Modules.Tenancy -s src/EnvLaw.Platform.Modules.Tenancy

docker build -f Dockerfile ../..   # image (context = repo root); CI job `platform-image` pushes :<sha> on main
```

Public REST contract: `openapi/v1.json`, generated on every `dotnet build` (`Microsoft.Extensions.ApiDescription.Server`; `PlatformOpenApi.cs` keeps only `/v1`, declares the API-key scheme and the 401/429 every endpoint shares). Commit it with the code change; CI fails when it is stale (`dotnet` job) and when it breaks the target branch's version (`openapi` job, oasdiff). A new `/v1` endpoint gets `.WithName` / `.WithTags` / `.WithSummary` / `.WithDescription` and a `.Produces…` per status it returns. An incompatible change goes to a new `/v2` group, not into `/v1`. Build-time generation runs `Program.cs` against a mock server; the only branch for it is the placeholder config at the top of `Program.cs`.

Contracts: `Modules.Ontology` compiles the repo-root `proto/env_law/ontology/v1/ontology.proto`, `Modules.Consult` compiles `proto/env_law/consult/v2/consult.proto` (Grpc.Tools; see each `.csproj`). Never copy a `.proto`.

Config (`Ontology` section): `Address` (in-cluster `dns:///env-law-ontology:50051` — headless Service, calls round-robin across pods; a plain `http://` address is a single host), `InternalToken` (must equal the service's `ONTOLOGY_INTERNAL_TOKEN`; secret), `Timeout`. Development uses `http://localhost:50051` + token `dev` (`ONTOLOGY_INTERNAL_TOKEN=dev uv run env-law-ontology` in `services/ontology/`).

Config (`Consult` section): same keys (`Address` in-cluster `dns:///env-law-consult:50052`; `InternalToken` = the service's `CONSULT_INTERNAL_TOKEN`; `Timeout`, default 10 s because consult itself calls ontology). Development uses `http://localhost:50052` + token `dev`. Both sections are required at startup. Both clients are registered by `SharedKernel.InternalGrpcClient.AddInternalGrpcClient` (internal token, tenant/request metadata, round-robin, h2c) — a new internal service client goes through it, not a copy.

Config (`Platform` section): `AdminKeySha256` (hex SHA-256 of the operator key; set via environment variable `Platform__AdminKeySha256`, never commit; the Api project has no `UserSecretsId`), `Plans:<name>:PermitLimit`/`Window` (built-in `free` = 60/min if absent), `TenantPlans:<tenant identifier>` = plan name (unlisted tenants are `free`; an unknown plan name fails startup), `ApplyMigrationsOnStartup` (Development/tests only). Connection string: `ConnectionStrings:Platform`; Npgsql also reads `PGUSER`/`PGPASSWORD`, so a deployment keeps the password out of the string.

Production migrations: EF Core migration bundles (the form EF Core's docs recommend for automated deployment), built into the image as `/app/migrate/tenancy` and `/app/migrate/platform` and run in that order by the deployment's migration Job. Each context has an `IDesignTimeDbContextFactory` sharing its `UseNpgsql` configuration with the app (`…Module.Configure`), so a bundle builds the context without running `Program.cs` and targets the same migrations history table.

- Package versions live only in `Directory.Packages.props` (central package management); `dotnet add package` updates it.
- Shared build settings: `Directory.Build.props`. Don't repeat them in `.csproj` files.

## Layout

- `src/EnvLaw.Platform.Api` — ASP.NET Core host (composition root)
- `src/EnvLaw.Platform.Modules.*` — feature modules (`Tenancy`, `ApiAccess`, `Metering`, `Ontology`, `Consult`); reference only `SharedKernel` and `Data`, never each other directly (cross-module contracts like `IUsageRecorder` live in `SharedKernel`)
- `src/EnvLaw.Platform.Data` — `PlatformDbContext`, its entities (`ApiKey`, `UsageRecord`) and migrations
- `src/EnvLaw.Platform.SharedKernel` — cross-module primitives (auth names, `IUsageRecorder`, internal gRPC client registration, design-time connection)
- `tests/EnvLaw.Platform.Tests` — xUnit + `WebApplicationFactory` integration tests (`PlatformFactory`: Testcontainers PostgreSQL + `FakeTimeProvider` + `FakeOntologyServer` / `FakeConsultServer`, in-process h2c gRPC fakes)

## Auth & tenancy flow

- Tenant API: `Authorization: Bearer sk_live_{prefix}_{secret}` → `ApiKeyAuthenticationHandler` (lookup by prefix, SHA-256 + constant-time compare) → `tenant` claim → Finbuckle resolves tenant → tenant-owned entities (`IsMultiTenant()`) are filtered automatically.
- Operator API (`/admin`): `X-Admin-Key` header, compared against `Platform:AdminKeySha256`.
- Middleware order is load-bearing: `UseAuthentication` → `UseMultiTenant` → `UseAuthorization` → `UseRateLimiter`.
- `/v1/laws/…` calls env-law-ontology over gRPC with `authorization: Bearer <InternalToken>`, `x-tenant-id`, `x-request-id`; gRPC `NotFound` → 404, `Unavailable`/`DeadlineExceeded` → 503. Only successful calls are metered (`IUsageRecorder`, one row per call in `platform.UsageRecords`).
- `POST /v1/ground` calls env-law-consult `Ground` the same way; a refusal is a 200 result and is metered (`consult.ground`); gRPC `InvalidArgument` → 400, `Unavailable`/`DeadlineExceeded` → 503 (not metered). Tests: `FakeConsultServer`.
- A `MultiTenantDbContext` captures the tenant at construction: never resolve it before tenant resolution; for a specific tenant outside a tenant request use `MultiTenantDbContext.Create<TContext, TenantInfo>(tenant, services)`.

## Conventions

- Formatting rules: `.editorconfig`.

## Safety

- Never commit secrets, connection strings, billing keys, or real `.env*` / local appsettings values.
- Every tenant-scoped query must be filtered by `TenantId`; never disable query filters without review.
- Bypass only the tenant filter by name (`IgnoreQueryFilters([TenantToken])`), and only in reviewed cross-tenant lookups (currently: API key authentication).
- Generated: `src/*/Migrations/` (change via `dotnet ef`, never by hand; marked `generated_code` in `.editorconfig`), gRPC code under `obj/` (from the repo-root proto), `openapi/v1.json` (from `dotnet build`; never by hand).
- Also ask before applying migrations to shared DBs (general ask-before list: root `AGENTS.md`).

## Done when

- `dotnet build` and `dotnet test` pass; `dotnet format --verify-no-changes` is clean — enforced by `.gitlab-ci.yml` (Docker-in-Docker for Testcontainers).
