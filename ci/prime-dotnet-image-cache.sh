#!/usr/bin/env bash
# 把 services/platform/Dockerfile restore 與建置會用到的套件全部下載進 $NUGET_PACKAGES，
# 映像建置不會再下載任何東西。
#
# 映像建置以 `--network=none` 執行並把這個資料夾當 build context，缺套件即失敗，不會連外網。
# 下面的指令與 Dockerfile 的相同（tool restore、專案 restore、兩個 migration bundle），所以套件集
# 不會與 Dockerfile 脫節：bundle 會帶進 runtime pack 與 Microsoft.NET.ILLink.Tasks，一般 restore 不會取。
#
# 從倉庫根執行並設定 NUGET_PACKAGES。RID 預設為 CI runner 的 linux-x64，Docker 的架構名由它推導。
# (linux-x64); the Docker architecture name is derived from it.
set -euo pipefail

: "${NUGET_PACKAGES:?set NUGET_PACKAGES to the package folder to fill}"
rid="${RID:-linux-x64}"
arch="$([ "$rid" = linux-x64 ] && echo amd64 || echo "${rid#linux-}")"
scratch="$(mktemp -d)"
cd "$(dirname "$0")/../services/platform"

dotnet tool restore
dotnet restore src/EnvCite.Platform.Api/EnvCite.Platform.Api.csproj -a "$arch"
dotnet ef migrations bundle -p src/EnvCite.Platform.Modules.Tenancy -s src/EnvCite.Platform.Modules.Tenancy \
  -r "$rid" -o "$scratch/tenancy"
dotnet ef migrations bundle -p src/EnvCite.Platform.Data -s src/EnvCite.Platform.Data \
  -r "$rid" -o "$scratch/platform"
rm -rf "$scratch"
