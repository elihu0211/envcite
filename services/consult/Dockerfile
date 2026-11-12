# Consult 映像，以倉庫根為建置情境（gRPC 契約從根目錄的 proto/ 編譯，共用套件在 libs/dotnet/）。
#
# 建置中不下載任何東西：套件來自 CI 的 `deps-dotnet` job 備好的 NuGet 資料夾
# （ci/prime-dotnet-image-cache.sh），以 build context `nuget` 傳入；碰到套件的步驟都以
# `--network=none` 執行，缺套件即失敗，不會連外網。資料夾是 bind mount，不會成為映像層。
#
#   NUGET_PACKAGES=$PWD/.nuget/packages ci/prime-dotnet-image-cache.sh
#   docker buildx build -f services/consult/Dockerfile --build-context nuget=.nuget/packages .
#
# SDK 階段在建置機的架構上執行並交叉編譯到目標（`-a`），不需要 emulation。SDK 版本與 platform 同一個，
# 理由見 services/platform/Dockerfile。
# renovate: datasource=docker depName=mcr.microsoft.com/dotnet/sdk
ARG DOTNET_VERSION=11.0.100
# registry 是參數：CI 讓基底映像只走 runner 主機的 pull-through 快取，沒有就失敗，不退回上游。
# 預設是真正的 registry，供本機建置。
ARG MCR=mcr.microsoft.com

FROM --platform=$BUILDPLATFORM ${MCR}/dotnet/sdk:${DOTNET_VERSION}-resolute AS build
ARG TARGETARCH
ENV NUGET_PACKAGES=/nuget
# NuGet Audit 是套件齊全時 restore 仍會有的唯一網路存取；弱點由 Renovate 監控，不在映像建置裡。
ENV NuGetAudit=false
# 對應倉庫的目錄結構：.csproj 以相對路徑找 proto/ 與 libs/dotnet/，Directory.*.props 在根。
WORKDIR /repo/services/consult
COPY global.json Directory.Build.props Directory.Packages.props .editorconfig /repo/
# 只用專案檔 restore，純原始碼變動時沿用這一層。restore 與 publish 的 `-a` 要一致。
COPY --parents services/consult/src/*/*.csproj libs/dotnet/*/*.csproj /repo/
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    dotnet restore src/EnvCite.Consult/EnvCite.Consult.csproj -a "$TARGETARCH"
# proto/ 在建置時編譯、不在 restore，所以放在 restore 層之後。
COPY proto /repo/proto
COPY libs/dotnet /repo/libs/dotnet
COPY services/consult/src ./src
RUN --network=none --mount=type=bind,from=nuget,target=/nuget,rw \
    dotnet publish src/EnvCite.Consult/EnvCite.Consult.csproj -c Release -a "$TARGETARCH" --no-restore -o /app

# Chiseled：沒有 shell 與套件管理員，預設非 root。沒有 ICU，專案以 InvariantGlobalization 對齊。
FROM ${MCR}/dotnet/aspnet:11.0-resolute-chiseled
WORKDIR /app
COPY --from=build /app .
# 與部署庫 base/consult 的 containerPort 與 gRPC 探針一致。
ENV ASPNETCORE_HTTP_PORTS=50052
EXPOSE 50052
ENTRYPOINT ["dotnet", "EnvCite.Consult.dll"]
