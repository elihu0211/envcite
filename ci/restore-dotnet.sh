#!/bin/sh
# .NET 依賴的全部備妥：確認 Nexus 鏡像可用、安裝 NuGet 設定、以鎖檔 restore 各 .NET 服務，最後備妥映像
# 建置要用的套件（ci/prime-dotnet-image-cache.sh）。
#
# 用法：sh ci/restore-dotnet.sh
#
# 套件只走 runner 主機的 Nexus（ci/nuget.mirror.config）；鏡像不可用就直接失敗，不退回上游，因為失敗的
# 請求也計入 Nexus 的每日額度。順序有意義：先探測、再動使用者層級的 NuGet 設定，探測失敗時不留下任何
# 改動；也因為會覆寫 ~/.nuget/NuGet/NuGet.Config，只在 CI 容器內執行。鎖檔釘住含間接依賴的完整依賴圖，
# 與清單不一致即失敗（--locked-mode）。
#
# 需要 sh、bash（探測用 /dev/tcp）、timeout、dotnet。不讀 CI 平台的變數；NuGet 自己的環境變數
# （NUGET_PACKAGES 等）由呼叫端設定。
set -eu

here=$(cd "$(dirname "$0")" && pwd)
cd "$here/.."

timeout 10 bash -c '</dev/tcp/host.docker.internal/8081' || {
  echo "Nexus 鏡像（host.docker.internal:8081）不可用；請檢查 runner 主機上的 Nexus。" >&2
  exit 1
}

mkdir -p ~/.nuget/NuGet
cp ci/nuget.mirror.config ~/.nuget/NuGet/NuGet.Config

for member in services/platform services/consult; do
  (cd "$member" && dotnet restore --locked-mode)
done

"$here/prime-dotnet-image-cache.sh"
