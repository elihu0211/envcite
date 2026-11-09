#!/bin/sh
# 產生物須與已提交的一致：先留一份目前的內容，重新產生，再比對。
#
# 用法：sh ci/check-regenerated.sh <目錄> <產生指令> [參數...]
#
# 在產生指令該執行的目錄執行；目錄與指令都相對於目前目錄。比對的是重新產生的前後，而不是 git 的狀態：
# 不需要 git（uv 映像沒有），也不受工作樹裡其他未提交檔案影響。__pycache__ 是 Python 執行的副產物，
# 不算產生物，從兩份副本去掉後才比，所以不依賴 GNU diff 才有的 -x（busybox 沒有）。產生指令失敗就照它的
# 結束碼失敗；內容不同時列出差異（committed 是已提交的、regenerated 是重新產生的）並說明該怎麼辦。
#
# 需要 sh、cp、find、diff、mktemp。不讀 CI 平台的變數。
set -eu

[ "$#" -ge 2 ] || { echo "用法：check-regenerated.sh <目錄> <產生指令> [參數...]" >&2; exit 2; }
dir=$1
shift

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

snapshot() { # 副本的名字
  mkdir "$work/$1"
  cp -r "$dir/." "$work/$1/"
  find "$work/$1" -name __pycache__ -type d -prune -exec rm -rf {} ';'
}

snapshot committed
"$@"
snapshot regenerated

status=0
(cd "$work" && diff -r committed regenerated) || status=$?
if [ "$status" -eq 1 ]; then
  echo "重新產生後，$dir 與已提交的內容不同；在本機執行「$*」，把結果一併提交。" >&2
fi
exit "$status"
