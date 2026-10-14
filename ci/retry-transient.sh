#!/bin/sh
# 執行一個指令；只在輸出顯示 registry／網路暫時性錯誤時重試，其餘失敗原樣結束。
#
# 用法：sh ci/retry-transient.sh <指令> [參數...]
#
# 為什麼不用 GitLab 的 `retry: when: script_failure`：它對所有 script 失敗一律重跑——
# 決定性的失敗（`dotnet restore` 缺套件、Dockerfile 寫錯）白跑兩次，且合併後才起跑、分支已刪的
# pipeline（`fatal: couldn't find remote ref`，exit 128）也各多跑兩次。這裡只認下面列的暫時性特徵。
#
# 特徵取自 2026-09-23 至 10-14 實際失敗的映像 job 日誌，全是連線層逾時：
#   registry.gitlab.com   `docker login`：context deadline exceeded／Client.Timeout exceeded
#   production.cloudfront.docker.com  拉基底層：dial tcp …: i/o timeout／timeout awaiting response headers
#   ghcr.io/token         dial tcp …: i/o timeout
# 加上常見的 5xx 與限流（toomanyrequests）。「失敗」與「逾時」同時出現才算，單有一個詞不夠。
#
# 退避：預設 30、60 秒（共最多 3 次）。2026-10-13 09:15 有 6 個 job 在同一分鐘失敗——那是 registry
# 短暫中斷，不是單次抖動，間隔太短會白重試。job timeout 是 30 分鐘，撐得住。
#
# 保留原結束碼；輸出即時串流到 stdout（同時存檔供比對）。
#
# 已知取捨：比對看整份輸出，若一次真正的建置失敗之前恰好有同樣字樣的警告（例如忽略錯誤的快取匯出
# 逾時），會多重試最多兩次；成本有限，層快取已在 registry，重試很快。
#
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
  echo "… 暫時性錯誤，${wait} 秒後重試（$n/$attempts）：$*" >&2
  sleep "$wait"
  n=$((n + 1))
  wait=$((wait * 2))
done
