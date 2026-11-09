#!/bin/sh
# ci/ 與 .githooks/ 的腳本不得讀 CI 平台的變數：平台資訊由 CI 設定檔轉成參數傳入，腳本才能原封
# 搬到別的平台，也才能在本機以同一指令重跑。
#   sh ci/test-platform-neutral.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
cd "$here/.." || exit 1

pattern='(^|[^A-Za-z0-9_])(CI_[A-Z0-9_]+|GITLAB_[A-Z0-9_]+|GITHUB_[A-Z0-9_]+)'

# 偵測式自己要先抓得到，否則「沒有命中」什麼都證明不了。
# shellcheck disable=SC2016  # 樣本要的就是不展開的字面值
if ! printf '%s\n' 'echo "$CI_COMMIT_SHA" "${GITHUB_SHA}" "$GITLAB_USER_ID"' | grep -qE "$pattern"; then
  echo "FAIL 偵測式抓不到平台變數"
  exit 1
fi

hits=
for f in ci/*.sh .githooks/*; do
  # 測試腳本要寫出比對的字樣，不在掃描範圍。
  case "$f" in */test-*) continue ;; esac
  found=$(grep -HnE "$pattern" "$f")
  [ -z "$found" ] || hits="$hits$found
"
done
if [ -n "$hits" ]; then
  echo "FAIL 腳本讀了 CI 平台的變數；改由呼叫端傳參數："
  echo "$hits"
  exit 1
fi
echo "ok   ci/ 與 .githooks/ 的腳本不讀 CI 平台變數"
