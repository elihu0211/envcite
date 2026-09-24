# Platform image. Build from the repo root — the gRPC contracts are compiled from
# the repo-root proto/, outside this directory:
#
#   docker build -f services/platform/Dockerfile .
#
# Carries the server (default entrypoint) and two EF Core migration bundles —
# the deployment form EF Core's docs recommend for automated deployment — which
# the migration Job runs in order: /app/migrate/tenancy, then /app/migrate/platform.
# Both build their DbContext through a design-time factory, not the app host, and
# read the same ConnectionStrings__Platform / PGUSER / PGPASSWORD as the server.
#
# The SDK stage runs on the build machine's architecture and cross-compiles for
# the target (`-a` / `-r`), the pattern in the official .NET Dockerfile samples:
# no emulation on an arm64 laptop or an amd64 CI runner.

FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:10.0-noble AS build
ARG TARGETARCH
# Mirrors the repo layout: each module's .csproj finds proto/ at ../../../../proto.
WORKDIR /repo/services/platform
COPY proto /repo/proto
COPY services/platform/global.json services/platform/Directory.Build.props services/platform/Directory.Packages.props services/platform/.editorconfig services/platform/dotnet-tools.json ./
COPY services/platform/src ./src
RUN dotnet tool restore \
 && dotnet publish src/EnvLaw.Platform.Api/EnvLaw.Platform.Api.csproj -c Release -a "$TARGETARCH" -o /app
# Bundles take a runtime identifier, not a Docker architecture name.
RUN rid="linux-$([ "$TARGETARCH" = amd64 ] && echo x64 || echo "$TARGETARCH")" \
 && dotnet ef migrations bundle -p src/EnvLaw.Platform.Modules.Tenancy -s src/EnvLaw.Platform.Modules.Tenancy \
      -r "$rid" -o /app/migrate/tenancy \
 && dotnet ef migrations bundle -p src/EnvLaw.Platform.Data -s src/EnvLaw.Platform.Data \
      -r "$rid" -o /app/migrate/platform

# Chiseled: no shell, no package manager, non-root ($APP_UID) by default. The
# -extra variant carries ICU and tzdata, which the default invariant mode lacks.
# The bundles are framework-dependent and run on this image's .NET runtime.
FROM mcr.microsoft.com/dotnet/aspnet:10.0-noble-chiseled-extra
WORKDIR /app
COPY --from=build /app .
EXPOSE 8080
ENTRYPOINT ["dotnet", "EnvLaw.Platform.Api.dll"]
