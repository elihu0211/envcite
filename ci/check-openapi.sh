#!/bin/sh
# 對外 REST 規格的相容性檢查：與基準分支已提交的規格比對 `oasdiff breaking`。
#
# 用法：sh ci/check-openapi.sh [--allow-breaking] <基準分支> <規格檔>
#
# 預設有 ERR 等級的破壞就失敗。`--allow-breaking` 用於刻意的破壞性變更：照樣列出破壞處供審查，
# 但不讓檢查失敗；要不要放行是呼叫端的政策（例如依 pull request 標籤），腳本不判斷。
#
# 基準規格從 origin 取回的基準分支讀出。基準分支還沒有該規格時沒有可比的東西，明示跳過；
# 取回失敗（網路、分支已刪）則直接失敗，否則檢查會在無人察覺時變成空操作。
# 規格與程式碼是否一致不在這裡檢查，由 platform 的建置驗證。
#
# 需要 sh、git、oasdiff；oasdiff 的版本由呼叫端決定。不讀 CI 平台的變數。
set -eu

allow=0
if [ "${1:-}" = "--allow-breaking" ]; then
  allow=1
  shift
fi
[ "$#" -eq 2 ] || { echo "用法：check-openapi.sh [--allow-breaking] <基準分支> <規格檔>" >&2; exit 2; }
base=$1
spec=$2

git fetch -q --depth=1 origin "$base"
found=$(git ls-tree --name-only FETCH_HEAD -- "$spec")
if [ -z "$found" ]; then
  echo "基準分支 $base 尚無 $spec，無基準可比——跳過相容性檢查"
  exit 0
fi

# 檔名帶規格名：oasdiff 的報告會以它標示「基準」那一側。
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
baseline="$dir/base-$(basename "$spec")"
git show "FETCH_HEAD:$spec" > "$baseline"

if [ "$allow" = 1 ]; then
  oasdiff breaking "$baseline" "$spec"
else
  oasdiff breaking "$baseline" "$spec" --fail-on ERR
fi
