#!/bin/sh
# 契約檢查：`buf lint`，並與基準分支比對 `buf breaking`。
#
# 用法：sh ci/check-proto.sh <基準分支>
#
# 基準先從 origin 取回，再以它的 commit 當 buf 的輸入：不經遠端網址與權杖，本機與 CI 跑的是同一支。
# 基準分支還沒有 buf.yaml（首次引入契約）時沒有可比的東西，明示跳過；取回失敗（網路、分支已刪）
# 則直接失敗，否則相容性檢查會在無人察覺時變成空操作。
#
# 需要 sh、git、buf；buf 的版本由呼叫端決定。不讀 CI 平台的變數，基準分支名由呼叫端傳入。
set -eu

[ "$#" -eq 1 ] || { echo "用法：check-proto.sh <基準分支>" >&2; exit 2; }
base=$1

buf lint

git fetch -q --depth=1 origin "$base"
found=$(git ls-tree --name-only FETCH_HEAD -- buf.yaml)
if [ -n "$found" ]; then
  buf breaking --against ".git#ref=$(git rev-parse FETCH_HEAD)"
else
  echo "基準分支 $base 尚無 buf.yaml，無基準可比——跳過相容性檢查"
fi
