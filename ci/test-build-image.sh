#!/bin/sh
# build-image.sh 的離線測試：假的 docker 記錄每次呼叫的參數與 stdin，不連網、不需要 docker。
#   sh ci/test-build-image.sh
# 推送路徑只在預設分支才會跑，MR 的 pipeline 驗不到，所以這裡逐字斷言「推送」與「只驗證」兩條路徑
# 收到的完整參數，是推送路徑合併前唯一的證據；失敗情境斷言錯誤沒有被吞掉、也沒有多做後面的步驟。
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

mkdir "$tmp/bin" "$tmp/tmpdir"
printf '127.0.0.1\tlocalhost\n172.18.0.2\tf3a9c1d2e4b5\n10.9.8.7\thost.docker.internal\n' > "$tmp/hosts"
printf '127.0.0.1\tlocalhost\n' > "$tmp/hosts-without-host"

# 假 docker：每次呼叫記一段（第一行 docker，其後每個參數一行，保留參數邊界）。
# 失敗與暫時性錯誤由 FAKE_LOGIN、FAKE_CREATE、FAKE_BUILD 指定。
cat > "$tmp/bin/docker" <<'FAKE'
#!/bin/sh
{
  echo docker
  prev=
  for a in "$@"; do
    # 設定檔路徑每次不同；內容在 create 當下另記。
    [ "$prev" = --buildkitd-config ] && a='<設定檔>'
    echo "  $a"
    prev=$a
  done
} >> "$CALLS"
kind=$1
[ "$1" = buildx ] && kind=$2
n=$(cat "$CALLS.$kind" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$CALLS.$kind"
case $kind in
  login)
    cat > "$STDIN_LOG"
    case ${FAKE_LOGIN:-ok} in
      deny)  echo 'Error response from daemon: unauthorized: authentication required' >&2; exit 1 ;;
      flaky) [ "$n" -ge 2 ] || { echo 'Get "https://registry.example/v2/": context deadline exceeded' >&2; exit 1; } ;;
    esac ;;
  create)
    # 設定檔在 create 之後就刪，所以在呼叫當下把內容記下來。
    while [ "$#" -gt 0 ]; do
      if [ "$1" = --buildkitd-config ]; then
        sed 's/^/    /' "$2" >> "$CALLS"
      fi
      shift
    done
    [ "${FAKE_CREATE:-ok}" = fail ] && { echo 'ERROR: failed to create builder' >&2; exit 1; } ;;
  build)
    case ${FAKE_BUILD:-ok} in
      fail)  echo 'ERROR: failed to solve: process "/bin/sh -c dotnet restore" did not complete successfully: exit code: 1' >&2; exit 7 ;;
      flaky) [ "$n" -ge 2 ] || { echo 'dial tcp 1.2.3.4:443: i/o timeout' >&2; exit 1; } ;;
    esac ;;
esac
exit 0
FAKE
chmod +x "$tmp/bin/docker"

secret='s3cret-pw-9f2a'
pw=$secret
hosts=$tmp/hosts
export CALLS="$tmp/calls" STDIN_LOG="$tmp/stdin" RETRY_DELAY=0 TMPDIR="$tmp/tmpdir"
export PATH="$tmp/bin:$PATH"

# 固定的一組參數；呼叫端要加的選項（--push 等）放在它們之前。
run() {
  : > "$CALLS"
  rm -f "$CALLS".* "$STDIN_LOG"
  HOSTS_FILE=$hosts REGISTRY_PASSWORD=$pw sh "$here/build-image.sh" "$@" \
    --service ontology --image registry.example/envcite/ontology:abc123 \
    --cache registry.example/envcite/cache/ontology \
    --cache-scope feature-x --cache-fallback-scope main \
    --registry registry.example --registry-user bot \
    -- --build-context uv-cache=.uv-cache --build-arg UV_VERSION=0.9.1 --build-arg PYTHON_VERSION=3.14 \
    > "$tmp/out" 2>&1
  rc=$?
}

# 只在這一次呼叫設定環境變數：不靠「變數前綴加函式呼叫」，各 shell 對它是否殘留的行為不同。
with() {
  name=$1
  export "$name=$2"
  shift 2
  "$@"
  unset "$name"
}

calls_of() { cat "$CALLS.$1" 2>/dev/null || echo 0; }
ok() { echo "ok   $1"; }
bad() {
  echo "FAIL $1"
  shift
  [ "$#" -eq 0 ] || printf '%s\n' "$@" | sed 's/^/     /'
  fail=1
}

# 預期的完整呼叫記錄；$1 是輸出旗標在記錄裡的樣子。
expected() {
  cat <<EOF
docker
  login
  -u
  bot
  --password-stdin
  registry.example
docker
  buildx
  create
  --use
  --driver
  docker-container
  --driver-opt
  network=host,image=10.9.8.7:5000/moby/buildkit:buildx-stable-1
  --buildkitd-flags
  --oci-worker-net=host
  --buildkitd-config
  <設定檔>
    [registry."10.9.8.7:5000"]
      http = true
      insecure = true
    [registry."10.9.8.7:5002"]
      http = true
      insecure = true
    [registry."10.9.8.7:5004"]
      http = true
      insecure = true
docker
  buildx
  build
  -f
  services/ontology/Dockerfile
$1
  --build-context
  uv-cache=.uv-cache
  --build-arg
  UV_VERSION=0.9.1
  --build-arg
  PYTHON_VERSION=3.14
  --build-arg
  DOCKER_HUB=10.9.8.7:5000
  --build-arg
  GHCR=10.9.8.7:5002
  --build-arg
  MCR=10.9.8.7:5004
  --cache-from
  type=registry,ref=registry.example/envcite/cache/ontology:feature-x
  --cache-from
  type=registry,ref=registry.example/envcite/cache/ontology:main
  --cache-to
  type=registry,ref=registry.example/envcite/cache/ontology:feature-x,mode=max,image-manifest=true,oci-mediatypes=true,ignore-error=true
  -t
  registry.example/envcite/ontology:abc123
  .
EOF
}

check_transcript() { # 名稱 預期的呼叫記錄檔
  if [ "$rc" != 0 ]; then
    bad "$1：結束碼 $rc" "$(cat "$tmp/out")"
  elif ! diff "$2" "$CALLS" > "$tmp/diff"; then
    bad "$1：docker 收到的參數與預期不同（< 預期，> 實際）" "$(cat "$tmp/diff")"
  else
    ok "$1"
  fi
}

# 失敗情境：結束碼、輸出字樣、各步驟被呼叫的次數（login create build）。
check_failure() { # 名稱 預期結束碼 輸出須含 預期次數
  want_rc=$2 want_out=$3 want_calls=$4
  got_calls="$(calls_of login) $(calls_of create) $(calls_of build)"
  if [ "$rc" != "$want_rc" ]; then bad "$1：結束碼 $rc（預期 $want_rc）" "$(cat "$tmp/out")"
  elif [ -n "$want_out" ] && ! grep -qF -e "$want_out" "$tmp/out"; then bad "$1：輸出沒有「$want_out」" "$(cat "$tmp/out")"
  elif [ "$got_calls" != "$want_calls" ]; then bad "$1：login／create／build 呼叫次數 $got_calls（預期 $want_calls）"
  else ok "$1"
  fi
}

# --- 兩條路徑的完整參數 ---

expected '  --push' > "$tmp/want-push"
printf '  --output\n  type=cacheonly\n' > "$tmp/output-flags"
expected "$(cat "$tmp/output-flags")" > "$tmp/want-verify"

run --push
check_transcript '推送：docker 收到的完整參數' "$tmp/want-push"

# 密碼：走 stdin，不進任何 argv，也不進日誌。
if [ "$(cat "$STDIN_LOG")" != "$secret" ]; then bad '密碼沒有從 stdin 交給 docker login'
elif grep -qF "$secret" "$CALLS"; then bad '密碼出現在 docker 的參數'
elif grep -qF "$secret" "$tmp/out"; then bad '密碼出現在輸出'
else ok '密碼只走 stdin，不在 argv 與輸出'
fi

if [ -z "$(ls -A "$tmp/tmpdir")" ]; then ok 'builder 設定檔用完即刪'
else bad 'builder 設定檔沒有清掉' "$(ls -A "$tmp/tmpdir")"
fi

run
check_transcript '只驗證：docker 收到的完整參數（沒有 --push）' "$tmp/want-verify"
if grep -qF '不推送' "$tmp/out"; then ok '只驗證：輸出說明不推送'; else bad '只驗證：輸出沒說明不推送'; fi

# --- 失敗不被吞掉，也不多做後面的步驟 ---

hosts=$tmp/hosts-without-host
run --push
check_failure '解析不到 host.docker.internal → 失敗、不建 builder' 1 'host.docker.internal' '1 0 0'
hosts=$tmp/hosts

with FAKE_LOGIN deny run --push
check_failure 'login 被拒 → 立刻失敗、不重試、不建 builder' 1 'unauthorized' '1 0 0'

with FAKE_CREATE fail run --push
check_failure '建 builder 失敗 → 失敗、不建置' 1 'failed to create builder' '1 1 0'

with FAKE_BUILD fail run --push
check_failure '建置失敗 → 保留原結束碼 7、不重試' 7 'dotnet restore' '1 1 1'

# --- 暫時性錯誤只重試出錯的那一步 ---

with FAKE_LOGIN flaky run --push
check_failure 'login 暫時性逾時 → 重試後成功' 0 '' '2 1 1'

with FAKE_BUILD flaky run --push
check_failure '建置暫時性逾時 → 只重試建置，不重登入、不重建 builder' 0 '' '1 1 2'

# --- 用法錯誤：不呼叫 docker ---

: > "$CALLS"; rm -f "$CALLS".*
sh "$here/build-image.sh" > "$tmp/out" 2>&1; rc=$?
check_failure '沒給參數 → 結束碼 2' 2 '用法' '0 0 0'

sh "$here/build-image.sh" --service > "$tmp/out" 2>&1; rc=$?
check_failure '選項缺值 → 結束碼 2' 2 '--service 需要一個值' '0 0 0'

sh "$here/build-image.sh" --bogus > "$tmp/out" 2>&1; rc=$?
check_failure '不認得的選項 → 結束碼 2' 2 '不認得的參數：--bogus' '0 0 0'

pw=
run --push
check_failure '沒有密碼 → 結束碼 2、不登入' 2 'REGISTRY_PASSWORD' '0 0 0'
pw=$secret

exit $fail
