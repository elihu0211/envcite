#!/usr/bin/env bash
# Downloads, into $NUGET_PACKAGES, everything services/platform/Dockerfile restores
# and builds with — and nothing else is ever downloaded for the image.
#
# The image build runs with `--network=none` and reads this folder as a build context,
# so a package missing here fails the build instead of reaching the network. The commands
# below are the Dockerfile's own (tool restore, project restore, the two migration
# bundles), so the set cannot drift from what the Dockerfile needs: the bundles are what
# pull the runtime packs and Microsoft.NET.ILLink.Tasks, which no plain restore fetches.
#
# Run from the repo root with NUGET_PACKAGES set. RID defaults to the CI runner's
# (linux-x64); the Docker architecture name is derived from it.
set -euo pipefail

: "${NUGET_PACKAGES:?set NUGET_PACKAGES to the package folder to fill}"
rid="${RID:-linux-x64}"
arch="$([ "$rid" = linux-x64 ] && echo amd64 || echo "${rid#linux-}")"
scratch="$(mktemp -d)"
cd "$(dirname "$0")/../services/platform"

dotnet tool restore
dotnet restore src/EnvLaw.Platform.Api/EnvLaw.Platform.Api.csproj -a "$arch"
dotnet ef migrations bundle -p src/EnvLaw.Platform.Modules.Tenancy -s src/EnvLaw.Platform.Modules.Tenancy \
  -r "$rid" -o "$scratch/tenancy"
dotnet ef migrations bundle -p src/EnvLaw.Platform.Data -s src/EnvLaw.Platform.Data \
  -r "$rid" -o "$scratch/platform"
rm -rf "$scratch"
