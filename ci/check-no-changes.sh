#!/bin/sh
# 路徑下不得有未提交的變更（含未追蹤的檔案）。
#
# 用法：sh ci/check-no-changes.sh <路徑> <產生該路徑的指令>
#
# 用於「由建置產生並提交」的檔案（如對外 REST 規格）：建置之後與已提交的內容不同，代表提交的是過期的
# 產生物。第二個參數只用在失敗訊息，告訴人該重跑什麼，腳本不執行它。路徑相對於目前目錄。
# git 本身失敗（不是 repo、沒有 git）也算失敗，不當成「沒有變更」，否則檢查會在無人察覺時變成空操作。
#
# 需要 sh、git。不讀 CI 平台的變數。
set -eu

[ "$#" -eq 2 ] || { echo "用法：check-no-changes.sh <路徑> <產生該路徑的指令>" >&2; exit 2; }
path=$1
cmd=$2

changes=$(git status --porcelain -- "$path")
if [ -n "$changes" ]; then
  echo "執行「$cmd」後，$path 與已提交的內容不同：" >&2
  echo "$changes" >&2
  echo "在本機執行「$cmd」，把 $path 的變更一併提交。" >&2
  exit 1
fi
echo "ok   $path 與已提交的內容一致"
