#!/bin/sh
# .NET 依賴的全部備妥：確認 Nexus 鏡像可用、安裝 NuGet 設定、以鎖檔 restore 各 .NET 服務，最後備妥映像
# 建置要用的套件（ci/prime-dotnet-image-cache.sh）。
#
# 用法：sh ci/restore-dotnet.sh [--nuget-config <檔>]
#
# 沒有 --nuget-config 時，套件只走 runner 主機的 Nexus（ci/nuget.mirror.config）；鏡像不可用就直接失敗，
# 不退回上游，因為失敗的請求也計入 Nexus 的每日額度。給了 --nuget-config 就用那份設定、不探測鏡像
# （GitHub-hosted runner 用 ci/nuget.org.config 直連 nuget.org）。順序有意義：先確定來源，再動使用者
# 層級的 NuGet 設定，探測失敗時不留下任何改動；也因為會覆寫 ~/.nuget/NuGet/NuGet.Config，只在 CI 內執行。
# 鎖檔釘住含間接依賴的完整依賴圖，與清單不一致即失敗（--locked-mode）。
#
# 需要 sh、bash（探測用 /dev/tcp）、timeout、dotnet。不讀 CI 平台的變數；NuGet 自己的環境變數
# （NUGET_PACKAGES 等）由呼叫端設定。
set -eu

here=$(cd "$(dirname "$0")" && pwd)
cd "$here/.."

nuget_config=
while [ "$#" -gt 0 ]; do
  case $1 in
    --nuget-config)
      [ "$#" -ge 2 ] || { echo "--nuget-config 需要一個值" >&2; exit 2; }
      nuget_config=$2
      shift 2
      ;;
    *) echo "不認得的參數：$1" >&2; exit 2 ;;
  esac
done

if [ -z "$nuget_config" ]; then
  timeout 10 bash -c '</dev/tcp/host.docker.internal/8081' || {
    echo "Nexus 鏡像（host.docker.internal:8081）不可用；請檢查 runner 主機上的 Nexus。" >&2
    exit 1
  }
  nuget_config=ci/nuget.mirror.config
else
  [ -f "$nuget_config" ] || { echo "找不到 NuGet 設定：$nuget_config" >&2; exit 2; }
fi

mkdir -p ~/.nuget/NuGet
cp "$nuget_config" ~/.nuget/NuGet/NuGet.Config

for member in services/platform services/consult; do
  (cd "$member" && dotnet restore --locked-mode)
done

"$here/prime-dotnet-image-cache.sh"
