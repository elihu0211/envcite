#!/bin/sh
# 提交前替暫存的 .cs 修好 CI 的 dotnet format 會擋的兩條規則：多餘的 using（IDE0005）與必要的大括號（IDE0011）。
# 只動「完全暫存」的檔案：還有未暫存修改的檔案不碰，否則 git add 會把不想提交的變更一起帶進去。
# 失敗不擋提交（CI 的 dotnet format --verify-no-changes 才是閘門），只提醒。
set -eu
set -f

cd "$(git rev-parse --show-toplevel)"

staged=$(git diff --cached --name-only --diff-filter=ACM -- '*.cs')
[ -n "$staged" ] || exit 0
partial=$(git diff --name-only -- '*.cs')

for svc in services/platform services/consult; do
  set --
  for f in $staged; do
    case "$f" in
      "$svc"/*)
        case "
$partial
" in
          *"
$f
"*) continue ;;
        esac
        set -- "$@" "${f#"$svc"/}"
        ;;
    esac
  done
  [ $# -gt 0 ] || continue

  if ! command -v dotnet >/dev/null 2>&1; then
    echo "format-staged：找不到 dotnet，略過 $svc（CI 仍會檢查）。" >&2
    continue
  fi

  if (cd "$svc" && dotnet format style --diagnostics IDE0005 IDE0011 --severity warn --no-restore --include "$@") >&2; then
    for f in "$@"; do
      git add -- "$svc/$f"
    done
  else
    echo "format-staged：$svc 自動修正失敗，提交照常；CI 的 dotnet format 會擋下未修正的部分。" >&2
  fi
done
