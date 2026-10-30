#!/usr/bin/env bash
# 把 .NET 服務（services/platform、services/consult）的 Dockerfile restore 與建置會用到的套件全部下載進
# $NUGET_PACKAGES，映像建置不會再下載任何東西。
#
# 映像建置以 `--network=none` 執行並把這個資料夾當 build context，缺套件即失敗，不會連外網。
# 下面的指令與各 Dockerfile 的相同（platform 的 tool restore、專案 restore、兩個 migration bundle；consult 的
# 專案 restore），所以套件集不會與 Dockerfile 脫節：bundle 會帶進 runtime pack 與 Microsoft.NET.ILLink.Tasks，
# 一般 restore 不會取。
#
# 從倉庫根執行並設定 NUGET_PACKAGES。RID 預設為 CI runner 的 linux-x64，Docker 的架構名由它推導。
#
# 已備妥就跳過：兩個 migration bundle 要編譯，快取命中時內容不會變，卻佔了約四分之三的時間。備妥後在
# $NUGET_PACKAGES 留一個標記檔，內容是決定套件集的輸入（SDK、套件與工具版本、鎖檔、專案檔、本腳本）
# 的雜湊；輸入一變雜湊就不同，重新備妥。標記檔隨 CI 的快取保存。PRIME_FORCE 非空則一律重做。
set -euo pipefail

: "${NUGET_PACKAGES:?set NUGET_PACKAGES to the package folder to fill}"
rid="${RID:-linux-x64}"
arch="$([ "$rid" = linux-x64 ] && echo amd64 || echo "${rid#linux-}")"
cd "$(dirname "$0")/.."

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi
}

# 路徑排序後逐檔取雜湊再合併，與檔案系統的列舉順序無關。RID 也是輸入：不同 RID 的 runtime pack 不同。
inputs_hash() {
  {
    echo "rid=$rid"
    {
      ls global.json Directory.Build.props Directory.Packages.props \
        services/platform/dotnet-tools.json ci/prime-dotnet-image-cache.sh
      find services libs/dotnet \( -name '*.csproj' -o -name packages.lock.json \) \
        -not -path '*/obj/*' -not -path '*/bin/*'
    } | LC_ALL=C sort | while read -r f; do sha256 "$f"; done
  } | sha256 | cut -d' ' -f1
}

stamp="$NUGET_PACKAGES/.primed"
want="$(inputs_hash)"
if [ -z "${PRIME_FORCE:-}" ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$want" ]; then
  echo "套件已備妥（$stamp 與輸入雜湊相符），略過。"
  exit 0
fi

scratch="$(mktemp -d)"

dotnet restore services/consult/src/EnvCite.Consult/EnvCite.Consult.csproj -a "$arch"

cd services/platform

dotnet tool restore
dotnet restore src/EnvCite.Platform.Api/EnvCite.Platform.Api.csproj -a "$arch"
dotnet ef migrations bundle -p src/EnvCite.Platform.Modules.Tenancy -s src/EnvCite.Platform.Modules.Tenancy \
  -r "$rid" -o "$scratch/tenancy"
dotnet ef migrations bundle -p src/EnvCite.Platform.Data -s src/EnvCite.Platform.Data \
  -r "$rid" -o "$scratch/platform"
rm -rf "$scratch"

# 全部成功才留標記：`set -e` 讓中途失敗直接結束，不會留下半套快取的標記。
echo "$want" > "$stamp"
