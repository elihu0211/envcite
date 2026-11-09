#!/bin/sh
# 映像建置：登入 registry、建立 buildx builder、建置；加 --push 才推送，否則只驗證建置。
#
# 用法：REGISTRY_PASSWORD=… sh ci/build-image.sh [--push] \
#         --service <服務名> --image <映像完整參照> --cache <層快取參照> \
#         --cache-scope <本分支的快取範圍> --cache-fallback-scope <後備的快取範圍> \
#         --registry <registry 位址> --registry-user <帳號> \
#         [-- <docker buildx build 的額外參數…>]
#
# 推不推送是呼叫端的政策（例如只有預設分支推送），腳本不判斷。`--` 之後原樣交給 docker buildx
# build：build-context、build-arg 隨服務與版本而變，由呼叫端決定，腳本不認得 Dockerfile 的內容。
#
# 層快取先讀本分支、再讀後備範圍，只寫本分支，各分支各寫各的、互不覆蓋；快取的讀寫失敗不讓建置失敗。
# 密碼只從環境變數 REGISTRY_PASSWORD 讀，以 stdin 交給 docker login，不進 argv 與日誌。
#
# 需要 sh、awk、docker（含 buildx）。不讀 CI 平台的變數。
# 環境變數（測試用，預設值即正式值）：HOSTS_FILE（/etc/hosts）。
set -eu

here=$(cd "$(dirname "$0")" && pwd)

# 自建 runner 主機上的 pull-through 快取（http），是 runner 的基礎設施而不是 CI 平台的功能。
# 基底映像與 buildkit 映像都不直連上游，快取沒有就失敗：Dockerfile 的 FROM 以 build-arg 指向這幾個
# 位址。不用 BuildKit 的 mirror 設定，它找不到時會退回原 registry，等於沒擋。
port_docker_hub=5000 # docker.io
port_ghcr=5002       # ghcr.io
port_mcr=5004        # mcr.microsoft.com

usage() {
  echo "用法：REGISTRY_PASSWORD=… build-image.sh [--push] --service <服務名> --image <映像參照> --cache <快取參照> --cache-scope <範圍> --cache-fallback-scope <範圍> --registry <位址> --registry-user <帳號> [-- buildx 額外參數…]" >&2
  exit 2
}
needs_value() { [ "$2" -ge 2 ] || { echo "$1 需要一個值" >&2; usage; }; }

push=0 service='' image='' cache='' scope='' fallback='' registry='' registry_user=''
while [ "$#" -gt 0 ]; do
  case $1 in
    --push) push=1; shift ;;
    --service) needs_value "$1" "$#"; service=$2; shift 2 ;;
    --image) needs_value "$1" "$#"; image=$2; shift 2 ;;
    --cache) needs_value "$1" "$#"; cache=$2; shift 2 ;;
    --cache-scope) needs_value "$1" "$#"; scope=$2; shift 2 ;;
    --cache-fallback-scope) needs_value "$1" "$#"; fallback=$2; shift 2 ;;
    --registry) needs_value "$1" "$#"; registry=$2; shift 2 ;;
    --registry-user) needs_value "$1" "$#"; registry_user=$2; shift 2 ;;
    --) shift; break ;;
    *) echo "不認得的參數：$1" >&2; usage ;;
  esac
done
[ -n "$service" ] && [ -n "$image" ] && [ -n "$cache" ] && [ -n "$scope" ] && [ -n "$fallback" ] \
  && [ -n "$registry" ] && [ -n "$registry_user" ] || usage
[ -n "${REGISTRY_PASSWORD:-}" ] || { echo "缺少環境變數 REGISTRY_PASSWORD" >&2; exit 2; }

# registry 偶發逾時：只對暫時性錯誤重試，見 retry-transient.sh。
sh "$here/retry-transient.sh" sh -c \
  'printf "%s\n" "$REGISTRY_PASSWORD" | docker login -u "$1" --password-stdin "$2"' sh "$registry_user" "$registry"

# 位址取 host.docker.internal 的解析值：buildkit 在 host 網路裡看不到 job 容器的 /etc/hosts，
# 所以不能把名稱交給它解析。
mirror_host=$(awk '/host\.docker\.internal/ {print $1; exit}' "${HOSTS_FILE:-/etc/hosts}")
[ -n "$mirror_host" ] || { echo "解析不到 host.docker.internal，無法使用 runner 主機的 registry 快取" >&2; exit 1; }

# buildkitd 的設定只用來放行上面幾個 http 位址。buildx 在 create 時就讀進設定，之後不再需要這個檔。
config=$(mktemp)
trap 'rm -f "$config"' EXIT
for port in "$port_docker_hub" "$port_ghcr" "$port_mcr"; do
  printf '[registry."%s:%s"]\n  http = true\n  insecure = true\n' "$mirror_host" "$port"
done > "$config"

# 匯出 registry 快取需要 docker-container driver，並用 host 網路：job 容器 MTU 1360、dind 橋接網路
# 1500，大封包被靜默丟棄（症狀：`dotnet restore` 連 api.nuget.org 逾時）。
docker buildx create --use --driver docker-container \
  --driver-opt "network=host,image=$mirror_host:$port_docker_hub/moby/buildkit:buildx-stable-1" \
  --buildkitd-flags '--oci-worker-net=host' --buildkitd-config "$config"

if [ "$push" = 1 ]; then
  set -- --push "$@"
else
  echo "只驗證建置、不推送：$image"
  set -- --output type=cacheonly "$@"
fi

# 只對暫時性逾時（拉基底層、ghcr token、推送）重試，建置失敗照舊立刻失敗；重試時層快取已在 registry。
# 各 Dockerfile 只用部分 build-arg，多餘的只會警告。
sh "$here/retry-transient.sh" docker buildx build -f "services/$service/Dockerfile" "$@" \
  --build-arg DOCKER_HUB="$mirror_host:$port_docker_hub" \
  --build-arg GHCR="$mirror_host:$port_ghcr" \
  --build-arg MCR="$mirror_host:$port_mcr" \
  --cache-from "type=registry,ref=$cache:$scope" \
  --cache-from "type=registry,ref=$cache:$fallback" \
  --cache-to "type=registry,ref=$cache:$scope,mode=max,image-manifest=true,oci-mediatypes=true,ignore-error=true" \
  -t "$image" .
