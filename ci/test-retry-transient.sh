#!/bin/sh
# retry-transient.sh 的離線測試：以假指令模擬各種輸出，不連網。
#   sh ci/test-retry-transient.sh
# 每個情境斷言「結束碼」與「指令被呼叫幾次」——後者才是重試邏輯的證據。
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export RETRY_DELAY=0 RETRY_ATTEMPTS=3 CALLS="$tmp/calls"
fail=0

# 假指令：依 $FAKE_MODE 輸出；計數寫進 $CALLS。
cat > "$tmp/fake" <<'FAKE'
#!/bin/sh
n=$(cat "$CALLS" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$CALLS"
case "$FAKE_MODE" in
  ok)        echo "built"; exit 0 ;;
  login)     echo 'Error response from daemon: Get "https://registry.gitlab.com/v2/": context deadline exceeded' >&2; exit 1 ;;
  clienttmo) echo 'Error response from daemon: Get "https://registry.gitlab.com/v2/": net/http: request canceled while waiting for connection (Client.Timeout exceeded while awaiting headers)' >&2; exit 1 ;;
  cdn)       echo 'ERROR: failed to build: failed to solve: failed to copy: httpReadSeeker: failed open: failed to do request: Get "https://production.cloudfront.docker.com/x": dial tcp 3.169.55.17:443: i/o timeout' >&2; exit 1 ;;
  ghcr)      echo '#2 ERROR: failed to authorize: failed to fetch anonymous token: Get "https://ghcr.io/token": dial tcp 140.82.113.33:443: i/o timeout' >&2; exit 1 ;;
  http503)   echo 'ERROR: failed to solve: received unexpected HTTP status: 503 Service Unavailable' >&2; exit 1 ;;
  flaky)     [ "$n" -ge 3 ] && { echo "built"; exit 0; }; echo 'dial tcp 1.2.3.4:443: i/o timeout' >&2; exit 1 ;;
  realfail)  echo 'ERROR: failed to solve: process "/bin/sh -c dotnet restore" did not complete successfully: exit code: 1' >&2; exit 7 ;;
  denied)    echo 'ERROR: failed to push: unauthorized: authentication required' >&2; exit 1 ;;
  gone)      echo "fatal: couldn't find remote ref refs/heads/x" >&2; exit 128 ;;
esac
FAKE
chmod +x "$tmp/fake"

check() { # 名稱 模式 預期結束碼 預期呼叫次數
  rm -f "$CALLS"
  FAKE_MODE=$2 sh "$here/retry-transient.sh" "$tmp/fake" >/dev/null 2>&1
  rc=$?; calls=$(cat "$CALLS" 2>/dev/null || echo 0)
  if [ "$rc" = "$3" ] && [ "$calls" = "$4" ]; then echo "ok   $1"
  else echo "FAIL $1：結束碼 $rc（預期 $3）、呼叫 $calls 次（預期 $4）"; fail=1; fi
}

check '成功 → 一次就過'                                   ok        0 1
check 'docker login 逾時 → 試滿 3 次後紅'                 login     1 3
check 'Client.Timeout（request canceled）→ 重試'          clienttmo 1 3
check 'cloudfront i/o timeout → 重試'                     cdn       1 3
check 'ghcr token i/o timeout → 重試'                     ghcr      1 3
check '503 → 重試'                                        http503   1 3
check '暫時性錯誤後恢復 → 第 3 次過'                      flaky     0 3
check '真正的建置失敗 → 立刻紅、保留原結束碼 7、不重試'   realfail  7 1
check '推送被拒（unauthorized）→ 立刻紅、不重試'          denied    1 1
check '分支已刪（exit 128）→ 立刻紅、不重試'              gone      128 1

# 輸出要即時串流到 stdout
out=$(rm -f "$CALLS"; FAKE_MODE=ok sh "$here/retry-transient.sh" "$tmp/fake" 2>&1)
if [ "$out" = "built" ]; then echo "ok   指令輸出原樣通過"; else echo "FAIL 指令輸出被改動：$out"; fail=1; fi
# 沒給指令要報用法錯誤
sh "$here/retry-transient.sh" >/dev/null 2>&1; rc=$?
if [ "$rc" = 2 ]; then echo "ok   沒給指令 → 結束碼 2"; else echo "FAIL 沒給指令的結束碼是 $rc"; fail=1; fi
exit $fail
