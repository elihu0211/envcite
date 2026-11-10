#!/bin/sh
# 執行一個指令；只在輸出顯示 registry／網路暫時性錯誤時重試，其餘失敗原樣結束。
#
# 用法：sh ci/retry-transient.sh <指令> [參數...]
#
# 不用「任何失敗都重跑」：那會把決定性的失敗（缺套件、
# Dockerfile 寫錯）與分支已刪的 CI（`fatal: couldn't find remote ref`，exit 128）都會白跑。
# 這裡只認連線層逾時的特徵：registry login 逾時、拉基底層與 ghcr token 的 i/o timeout，
# 加上常見的 5xx 與限流。「失敗」與「逾時」同時出現才算，單有一個詞不夠。
#
# 退避：預設 30、60 秒（最多 3 次）。registry 短暫中斷會讓多個 job 同時失敗，間隔太短會白重試；
# job timeout 是 30 分鐘，撐得住。
#
# 保留原結束碼；輸出即時串流到 stdout（同時存檔供比對）。
#
# 已知取捨：比對看整份輸出，真正的建置失敗之前若有同樣字樣的警告（例如忽略錯誤的快取匯出逾時），
# 會多重試最多兩次；層快取已在 registry，成本有限。
#
# 環境變數（測試用，預設值即正式值）：RETRY_ATTEMPTS（3）、RETRY_DELAY（30，之後每次加倍）。
# 環境變數（測試用，預設值即正式值）：RETRY_ATTEMPTS（3）、RETRY_DELAY（30，之後每次加倍）。
set -u

[ "$#" -gt 0 ] || { echo "用法：retry-transient.sh <指令> [參數...]" >&2; exit 2; }

attempts=${RETRY_ATTEMPTS:-3}
wait=${RETRY_DELAY:-30}
transient='context deadline exceeded|Client\.Timeout exceeded|request canceled while waiting for connection|timeout awaiting response headers|TLS handshake timeout|i/o timeout|connection reset by peer|unexpected HTTP status: 50[0234]|toomanyrequests|too many requests'

out=$(mktemp)
rcfile=$(mktemp)
trap 'rm -f "$out" "$rcfile"' EXIT

n=1
while :; do
  # 管線的結束碼是 tee 的；指令的結束碼另存檔案取回。
  { "$@" 2>&1; echo $? > "$rcfile"; } | tee "$out"
  rc=$(cat "$rcfile")
  [ "$rc" = 0 ] && exit 0

  if ! grep -Eqi "$transient" "$out"; then
    exit "$rc"
  fi
  if [ "$n" -ge "$attempts" ]; then
    echo "… 暫時性錯誤，已試 $n 次仍失敗，放棄" >&2
    exit "$rc"
  fi
  echo "… 暫時性錯誤，${wait} 秒後重試（${n}/${attempts}）：$*" >&2
  sleep "$wait"
  n=$((n + 1))
  wait=$((wait * 2))
done
