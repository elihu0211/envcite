#!/bin/sh
# 預設分支上不改動：先開分支，走 MR（見 CONTRIBUTING.md「從開發到上線」）。
# 以 exit 2 結束：git 視為失敗，Claude Code hook 視為阻擋。
default=$(git symbolic-ref --short -q refs/remotes/origin/HEAD | sed 's|^origin/||')
if [ "$(git symbolic-ref --short -q HEAD)" = "${default:-main}" ]; then
  echo "拒絕在 ${default:-main} 上 $1：先開分支（wt switch --create <type>/<簡述>）。" >&2
  exit 2
fi
