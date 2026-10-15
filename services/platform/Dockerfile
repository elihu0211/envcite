# Platform 映像，以倉庫根為建置情境（gRPC 契約從根目錄的 proto/ 編譯）。
#
# 內容：伺服器（預設進入點）與兩個 EF Core migration bundle（EF Core 文件建議的自動部署形式）。
# 遷移 Job 依序執行 /app/migrate/tenancy、/app/migrate/platform；bundle 以 design-time factory
# 建 DbContext，連線取自與伺服器相同的 ConnectionStrings__Platform／PGUSER／PGPASSWORD。
#
# SDK 階段在建置機的架構上執行並交叉編譯到目標（`-a`／`-r`），不需要 emulation。

# 建置中不下載任何東西：套件來自 CI 的 `deps-dotnet` job 備好的 NuGet 資料夾
# （ci/prime-dotnet-image-cache.sh），以 build context `nuget` 傳入；碰到套件的步驟都以
# `--network=none` 執行，缺套件即失敗，不會連外網。資料夾是 bind mount，不會成為映像層。
#
#   NUGET_PACKAGES=$PWD/.nuget/packages ci/prime-dotnet-image-cache.sh
#   docker buildx build -f services/platform/Dockerfile --build-context nuget=.nuget/packages .
#
# SDK 版本固定，CI 的套件快取鍵也帶它：bundle 要的套件（runtime pack、ILLink tasks）隨 SDK，
# 浮動 tag 會讓映像與快取不一致。
# renovate: datasource=docker depName=mcr.microsoft.com/dotnet/sdk
ARG DOTNET_VERSION=10.0.402
# registry 是參數：CI 讓基底映像只走 runner 主機的 pull-through 快取，沒有就失敗，不退回上游。
# 預設是真正的 registry，供本機建置。
ARG MCR=mcr.microsoft.com

FROM --platform=$BUILDPLATFORM ${MCR}/dotnet/sdk:${DOTNET_VERSION}-noble AS build
ARG TARGETARCH
ENV NUGET_PACKAGES=/nuget
# NuGet Audit 在 restore 時向 nuget.org 查弱點（連不上是 NU1900，TreatWarningsAsErrors 下為錯誤），
# 是套件齊全時 restore 仍會有的唯一網路存取。弱點由 Renovate 的 `osvVulnerabilityAlerts` 監控，不在映像建置裡。
ENV NuGetAudit=false
# 對應倉庫的目錄結構：各模組的 .csproj 在 ../../../../proto 找 proto/。
WORKDIR /repo/services/platform
COPY services/platform/global.json services/platform/Directory.Build.props services/platform/Directory.Packages.props services/platform/.editorconfig services/platform/dotnet-tools.json ./
# 只用專案檔 restore，純原始碼變動時沿用這一層。--parents 保留各專案的路徑，glob 涵蓋 Api 專案
# 引用的所有專案。restore 與 publish 的 `-a` 要一致，否則 publish 會再 restore 一次。
COPY --parents services/platform/src/*/*.csproj /repo/
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    dotnet tool restore \
 && dotnet restore src/EnvCite.Platform.Api/EnvCite.Platform.Api.csproj -a "$TARGETARCH"
# proto/ 在建置時編譯、不在 restore，所以放在 restore 層之後。
COPY proto /repo/proto
COPY services/platform/src ./src
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    dotnet publish src/EnvCite.Platform.Api/EnvCite.Platform.Api.csproj -c Release -a "$TARGETARCH" --no-restore -o /app
# bundle 吃 runtime identifier，不是 Docker 的架構名。產生 bundle 會 restore 自己的專案，需要
# runtime pack 與 ILLink tasks，prime 腳本以同樣兩個指令取得。
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    rid="linux-$([ "$TARGETARCH" = amd64 ] && echo x64 || echo "$TARGETARCH")" \
 && dotnet tool restore \
 && dotnet ef migrations bundle -p src/EnvCite.Platform.Modules.Tenancy -s src/EnvCite.Platform.Modules.Tenancy \
      -r "$rid" -o /app/migrate/tenancy \
 && dotnet ef migrations bundle -p src/EnvCite.Platform.Data -s src/EnvCite.Platform.Data \
      -r "$rid" -o /app/migrate/platform

# Chiseled：沒有 shell 與套件管理員，預設非 root（$APP_UID）。-extra 變體帶 ICU 與 tzdata，
# 預設的 invariant 模式沒有。bundle 是 framework-dependent，用本映像的 .NET 執行期跑。
FROM ${MCR}/dotnet/aspnet:10.0-noble-chiseled-extra
WORKDIR /app
COPY --from=build /app .
EXPOSE 8080
ENTRYPOINT ["dotnet", "EnvCite.Platform.Api.dll"]
