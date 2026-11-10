#!/bin/sh
# restore-dotnet.sh 的離線測試：暫時 repo 與假的 timeout、dotnet、備妥腳本，不連網、不需要 dotnet。
#   sh ci/test-restore-dotnet.sh
# 每個情境斷言完整的呼叫順序——順序（先探測、再裝設定、最後才備妥）與「失敗後不做後面的步驟」就是
# 這支腳本的價值。
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

repo=$tmp/repo
mkdir -p "$repo/ci" "$repo/services/platform" "$repo/services/consult" "$tmp/bin" "$tmp/home"
cp "$here/restore-dotnet.sh" "$repo/ci/"
echo 'nexus-config-marker' > "$repo/ci/nuget.mirror.config"
repo=$(cd "$repo" && pwd -P)

export HOME="$tmp/home" CALLS="$tmp/calls" SRC_CONFIG="$repo/ci/nuget.mirror.config" REPO="$repo"

# 備妥腳本：只記一行。FAKE_PRIME=fail 讓它失敗。
cat > "$repo/ci/prime-dotnet-image-cache.sh" <<'FAKE'
#!/bin/sh
echo "prime" >> "$CALLS"
[ "${FAKE_PRIME:-ok}" = fail ] && { echo 'prime failed' >&2; exit 4; }
exit 0
FAKE
chmod +x "$repo/ci/prime-dotnet-image-cache.sh"

# timeout：記下完整參數，不真的執行 bash；FAKE_PROBE=down 模擬鏡像不通。
cat > "$tmp/bin/timeout" <<'FAKE'
#!/bin/sh
echo "timeout $*" >> "$CALLS"
[ "${FAKE_PROBE:-up}" = down ] && { echo 'bash: connect: Connection refused' >&2; exit 1; }
exit 0
FAKE
# dotnet：記下執行的目錄、參數，以及「呼叫當下 NuGet 設定是否已就位」；FAKE_DOTNET_FAIL 指定在哪個目錄失敗。
cat > "$tmp/bin/dotnet" <<'FAKE'
#!/bin/sh
dir=$(pwd -P)
dir=${dir#"$REPO"/}
if cmp -s "$HOME/.nuget/NuGet/NuGet.Config" "$SRC_CONFIG"; then cfg=設定已就位; else cfg=設定未就位; fi
echo "dotnet [$dir] $* ($cfg)" >> "$CALLS"
[ "${FAKE_DOTNET_FAIL:-}" = "$dir" ] && { echo 'error NU1004: 鎖檔與清單不一致' >&2; exit 1; }
exit 0
FAKE
chmod +x "$tmp/bin/timeout" "$tmp/bin/dotnet"
export PATH="$tmp/bin:$PATH"

run() {
  : > "$CALLS"
  rm -rf "$HOME/.nuget"
  (cd "$repo" && sh ci/restore-dotnet.sh) > "$tmp/out" 2>&1
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

ok() { echo "ok   $1"; }
bad() {
  echo "FAIL $1"
  shift
  [ "$#" -eq 0 ] || printf '%s\n' "$@" | sed 's/^/     /'
  fail=1
}

check() { # 名稱 預期結束碼（0 或 nonzero） 預期呼叫記錄 輸出須含的字樣（可空）
  printf '%s\n' "$3" > "$tmp/want"
  if [ "$2" = 0 ] && [ "$rc" != 0 ]; then bad "$1：結束碼 $rc（預期 0）" "$(cat "$tmp/out")"
  elif [ "$2" != 0 ] && [ "$rc" = 0 ]; then bad "$1：預期失敗卻成功了"
  elif ! diff "$tmp/want" "$CALLS" > "$tmp/diff"; then bad "$1：呼叫記錄與預期不同（< 預期，> 實際）" "$(cat "$tmp/diff")"
  elif [ -n "${4:-}" ] && ! grep -qF -e "$4" "$tmp/out"; then bad "$1：輸出沒有「$4」" "$(cat "$tmp/out")"
  else ok "$1"
  fi
}

probe='timeout 10 bash -c </dev/tcp/host.docker.internal/8081'
platform='dotnet [services/platform] restore --locked-mode (設定已就位)'
consult='dotnet [services/consult] restore --locked-mode (設定已就位)'

run
check '全部成功：探測 → 裝設定 → 兩個成員 restore → 備妥' 0 "$probe
$platform
$consult
prime"
if cmp -s "$HOME/.nuget/NuGet/NuGet.Config" "$repo/ci/nuget.mirror.config"; then ok '使用者層級的 NuGet.Config 就是 ci/nuget.mirror.config'
else bad '使用者層級的 NuGet.Config 與 ci/nuget.mirror.config 不同'
fi

with FAKE_PROBE down run
check '鏡像不通 → 失敗並說明，什麼都不動' nonzero "$probe" 'Nexus 鏡像（host.docker.internal:8081）不可用'
if [ -e "$HOME/.nuget" ]; then bad '鏡像不通卻動了使用者層級的 NuGet 設定'; else ok '鏡像不通時沒有動 NuGet 設定'; fi

with FAKE_DOTNET_FAIL services/platform run
check 'platform 的鎖檔與清單不一致 → 失敗，不再 restore consult、不備妥' nonzero "$probe
$platform" 'NU1004'

with FAKE_DOTNET_FAIL services/consult run
check 'consult 的鎖檔與清單不一致 → 失敗，不備妥' nonzero "$probe
$platform
$consult" 'NU1004'

with FAKE_PRIME fail run
check '備妥失敗 → 失敗' nonzero "$probe
$platform
$consult
prime" 'prime failed'

echo 'public-config-marker' > "$repo/ci/nuget.org.config"
export SRC_CONFIG="$repo/ci/nuget.org.config"
: > "$CALLS"
rm -rf "$HOME/.nuget"
(cd "$repo" && sh ci/restore-dotnet.sh --nuget-config ci/nuget.org.config) > "$tmp/out" 2>&1
rc=$?
check '指定 nuget 設定：不探測鏡像，直接 restore' 0 "$platform
$consult
prime"

exit $fail
