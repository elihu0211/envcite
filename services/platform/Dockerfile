# Platform image. Build from the repo root — the gRPC contracts are compiled from
# the repo-root proto/, outside this directory. It needs the NuGet package folder as a
# build context; the commands are below, under "Nothing is downloaded here".
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

# Nothing is downloaded here. Packages come from the NuGet folder that CI's `deps-dotnet`
# job filled (ci/prime-dotnet-image-cache.sh), passed as the build context `nuget`, and
# every step that touches them runs with `--network=none`: a package missing from that
# folder fails the build instead of reaching the network. The folder is bind-mounted, so
# it never becomes an image layer (and never reaches the registry layer cache).
#
#   NUGET_PACKAGES=$PWD/.nuget/packages ci/prime-dotnet-image-cache.sh
#   docker buildx build -f services/platform/Dockerfile --build-context nuget=.nuget/packages .
#
# The SDK version is pinned, and CI keys the package cache by it: the bundles' packages
# (runtime packs, ILLink tasks) follow the SDK, so a floating tag would let the image
# build and the cache disagree.
# renovate: datasource=docker depName=mcr.microsoft.com/dotnet/sdk
ARG DOTNET_VERSION=10.0.401

FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:${DOTNET_VERSION}-noble AS build
ARG TARGETARCH
ENV NUGET_PACKAGES=/nuget
# NuGet Audit queries nuget.org's vulnerability feed at restore (NU1900, an error under
# TreatWarningsAsErrors, when it cannot be reached): the one network access a restore
# makes even with every package in hand. Vulnerabilities are watched by Renovate
# (`osvVulnerabilityAlerts`), not by the image build.
ENV NuGetAudit=false
# Mirrors the repo layout: each module's .csproj finds proto/ at ../../../../proto.
WORKDIR /repo/services/platform
COPY services/platform/global.json services/platform/Directory.Build.props services/platform/Directory.Packages.props services/platform/.editorconfig services/platform/dotnet-tools.json ./
# Restore from the project files alone, so a source-only change reuses this layer.
# --parents keeps each project's path; the glob covers every project the Api project
# references. Restore and publish must agree on -a, or publish restores again.
COPY --parents services/platform/src/*/*.csproj /repo/
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    dotnet tool restore \
 && dotnet restore src/EnvLaw.Platform.Api/EnvLaw.Platform.Api.csproj -a "$TARGETARCH"
# proto/ is compiled at build, not at restore, so it comes after the restore layer.
COPY proto /repo/proto
COPY services/platform/src ./src
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    dotnet publish src/EnvLaw.Platform.Api/EnvLaw.Platform.Api.csproj -c Release -a "$TARGETARCH" --no-restore -o /app
# Bundles take a runtime identifier, not a Docker architecture name. Generating a bundle
# restores its own project and needs the runtime packs and ILLink tasks, which the prime
# script fetches by running these same two commands.
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    rid="linux-$([ "$TARGETARCH" = amd64 ] && echo x64 || echo "$TARGETARCH")" \
 && dotnet tool restore \
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
