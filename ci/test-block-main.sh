#!/bin/sh
# .githooks/block-main.sh 的離線測試：用暫時的 git repo 與 worktree，不連網。
#   sh ci/test-block-main.sh
# 重點情境：hook 的工作目錄在 main 工作樹、被編輯檔卻在別的 worktree（不可誤擋）。
set -u
here=$(cd "$(dirname "$0")" && pwd)
script="$here/../.githooks/block-main.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

git init -q -b main "$tmp/main"
git -C "$tmp/main" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
git -C "$tmp/main" update-ref refs/remotes/origin/main HEAD
git -C "$tmp/main" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git -C "$tmp/main" worktree add -q -b feat/x "$tmp/wt"
mkdir "$tmp/plain"

check() { # 名稱 預期結束碼 工作目錄 stdin（空字串 = 無 stdin）
  if [ -n "$4" ]; then
    (cd "$3" && printf '%s' "$4" | sh "$script" 改動 >/dev/null 2>&1)
  else
    (cd "$3" && sh "$script" commit </dev/null >/dev/null 2>&1)
  fi
  rc=$?
  if [ "$rc" = "$2" ]; then echo "ok   $1"; else echo "FAIL $1：結束碼 ${rc}（預期 ${2}）"; fail=1; fi
}
json() { printf '{"tool_name":"Edit","tool_input":{"file_path":"%s"}}' "$1"; }

check 'main 工作樹的檔案 → 擋'                          2 "$tmp/main" "$(json "$tmp/main/a.txt")"
check 'worktree 的檔案，工作目錄在 main → 放行（本次修正）' 0 "$tmp/main" "$(json "$tmp/wt/a.txt")"
check 'worktree 的新檔（目錄尚未存在）→ 放行'            0 "$tmp/main" "$(json "$tmp/wt/new/dir/a.txt")"
check 'main 工作樹的新檔（目錄尚未存在）→ 擋'            2 "$tmp/wt"   "$(json "$tmp/main/new/dir/a.txt")"
check 'NotebookEdit 的 notebook_path 同樣依檔案判斷'      2 "$tmp/wt"   '{"tool_name":"NotebookEdit","tool_input":{"notebook_path":"'"$tmp"'/main/n.ipynb"}}'
check '不在 git 工作樹的檔案 → 放行'                      0 "$tmp/main" "$(json "$tmp/plain/a.txt")"
check '無 stdin（git hook）在 main → 擋'                  2 "$tmp/main" ''
check '無 stdin（git hook）在 worktree → 放行'            0 "$tmp/wt"   ''
check 'JSON 缺 file_path → 退回目前目錄（main）→ 擋'      2 "$tmp/main" '{"tool_name":"Bash","tool_input":{"command":"ls"}}'

exit $fail
