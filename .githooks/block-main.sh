#!/bin/sh
# 預設分支上不改動：先開分支，走 pull request（見 CONTRIBUTING.md「從開發到上線」）。
# 以 exit 2 結束：git 視為失敗，Claude Code hook 視為阻擋。
#
# 兩種呼叫：
#   - git hook（pre-commit）：無 stdin，判斷目前目錄的分支。
#   - Claude Code PreToolUse hook：stdin 為 JSON，檔案路徑在 tool_input.file_path
#     （Edit、Write；NotebookEdit 為 notebook_path）。hook 的工作目錄可能在主 checkout，
#     與被編輯檔所在的 worktree 無關，所以要依檔案所在的工作樹判斷。
dir=.
if [ ! -t 0 ]; then
  input=$(cat)
  if [ -n "$input" ]; then
    if command -v jq >/dev/null 2>&1; then
      file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null)
    elif command -v python3 >/dev/null 2>&1; then
      file=$(printf '%s' "$input" | python3 -I -c '
import json, sys
try:
    t = json.load(sys.stdin).get("tool_input") or {}
    print(t.get("file_path") or t.get("notebook_path") or "")
except Exception:
    pass' 2>/dev/null)
    fi
    if [ -n "${file:-}" ]; then
      dir=$(dirname "$file")
      # 新檔的目錄可能還不存在：往上找最近存在的父目錄。
      while [ ! -d "$dir" ] && [ "$dir" != "/" ] && [ "$dir" != "." ]; do
        dir=$(dirname "$dir")
      done
    fi
  fi
fi

# 不在 git 工作樹內（例如 repo 外的檔案）：放行。
git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

default=$(git -C "$dir" symbolic-ref --short -q refs/remotes/origin/HEAD | sed 's|^origin/||')
if [ "$(git -C "$dir" symbolic-ref --short -q HEAD)" = "${default:-main}" ]; then
  echo "拒絕在 ${default:-main} 上 $1：先開分支（wt switch --create <type>/<簡述>）。" >&2
  exit 2
fi
