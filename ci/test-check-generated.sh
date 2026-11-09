#!/bin/sh
# check-no-changes.sh、check-regenerated.sh 的離線測試：暫時 repo 與暫時目錄，不連網。
#   sh ci/test-check-generated.sh
# 每個情境斷言結束碼與訊息：「過期的產生物被抓到」與「檢查本身壞了也不會放行」是這兩支腳本的價值。
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
mkdir "$tmp/tmpdir"
export TMPDIR="$tmp/tmpdir"

ok() { echo "ok   $1"; }
bad() {
  echo "FAIL $1"
  shift
  [ "$#" -eq 0 ] || printf '%s\n' "$@" | sed 's/^/     /'
  fail=1
}

check() { # 名稱 預期結束碼 輸出須含的字樣（可空） 輸出不得含的字樣（可空）；實際結果在 $rc 與 $tmp/out
  if [ "$rc" != "$2" ]; then bad "$1：結束碼 $rc（預期 $2）" "$(cat "$tmp/out")"
  elif [ -n "${3:-}" ] && ! grep -qF -e "$3" "$tmp/out"; then bad "$1：輸出沒有「$3」" "$(cat "$tmp/out")"
  elif [ -n "${4:-}" ] && grep -qF -e "$4" "$tmp/out"; then bad "$1：輸出不該有「$4」" "$(cat "$tmp/out")"
  else ok "$1"
  fi
}

has_message() { # 名稱 輸出須含的字樣
  if grep -qF -e "$2" "$tmp/out"; then ok "$1"; else bad "$1" "$(cat "$tmp/out")"; fi
}

# --- check-no-changes.sh ---

mkrepo() {
  rm -rf "$tmp/work"
  git init -q -b main "$tmp/work"
  mkdir "$tmp/work/gen" "$tmp/work/other"
  echo '{"v":1}' > "$tmp/work/gen/spec.json"
  echo x > "$tmp/work/other/x"
  git -C "$tmp/work" add -A
  git -C "$tmp/work" commit -q -m base
}
no_changes() { # 在 work 內執行
  (cd "$tmp/work" && sh "$here/check-no-changes.sh" "$@") > "$tmp/out" 2>&1
  rc=$?
}

mkrepo
no_changes gen 'dotnet build'
check 'no-changes：沒有變更 → 通過' 0 'ok   gen'

echo '{"v":2}' > "$tmp/work/gen/spec.json"
no_changes gen 'dotnet build'
check 'no-changes：已追蹤的檔案被改 → 失敗，列出檔案與該重跑的指令' 1 'M gen/spec.json'
has_message 'no-changes：訊息說明該怎麼辦' '在本機執行「dotnet build」'

mkrepo
echo '{}' > "$tmp/work/gen/new.json"
no_changes gen 'dotnet build'
check 'no-changes：多出未追蹤的檔案 → 失敗' 1 '?? gen/new.json'

mkrepo
echo y > "$tmp/work/other/x"
no_changes gen 'dotnet build'
check 'no-changes：路徑之外的變更不算 → 通過' 0 'ok   gen'

mkdir "$tmp/plain"
(cd "$tmp/plain" && GIT_CEILING_DIRECTORIES="$tmp" sh "$here/check-no-changes.sh" gen 'dotnet build') > "$tmp/out" 2>&1
rc=$?
check 'no-changes：git 本身失敗（不是 repo）→ 失敗，不當成沒有變更' 128 'not a git repository' 'ok   '

sh "$here/check-no-changes.sh" gen > "$tmp/out" 2>&1; rc=$?
check 'no-changes：缺參數 → 結束碼 2' 2 '用法'

# --- check-regenerated.sh ---

mkdir "$tmp/gen"
# 假的產生指令：依第一個參數改寫目錄。用相對路徑執行，證明指令與目錄都相對於目前目錄。
cat > "$tmp/gen/gen.sh" <<'FAKE'
#!/bin/sh
case $1 in
  same)    echo 'v1' > src/a.py ;;
  pycache) mkdir -p src/__pycache__ && echo 'x' > src/__pycache__/a.pyc ;;
  change)  echo 'v2' > src/a.py ;;
  add)     echo 'v1' > src/a.py; echo 'new' > src/b.py ;;
  remove)  rm src/a.py ;;
  fail)    echo '產生器壞了' >&2; exit 3 ;;
esac
FAKE
chmod +x "$tmp/gen/gen.sh"
regen() { # 模式
  rm -rf "$tmp/gen/src"
  mkdir -p "$tmp/gen/src/__pycache__"
  echo 'v1' > "$tmp/gen/src/a.py"
  echo 'old' > "$tmp/gen/src/__pycache__/a.pyc"
  (cd "$tmp/gen" && sh "$here/check-regenerated.sh" src ./gen.sh "$1") > "$tmp/out" 2>&1
  rc=$?
}

regen same
check 'regenerated：重新產生後內容相同 → 通過' 0
regen pycache
check 'regenerated：只有 __pycache__ 不同 → 通過' 0
regen change
check 'regenerated：內容不同 → 失敗，列出差異' 1 'v2'
has_message 'regenerated：訊息說明該怎麼辦' '在本機執行「./gen.sh change」'
regen add
check 'regenerated：多出檔案 → 失敗' 1 'Only in regenerated: b.py'
regen remove
check 'regenerated：少了檔案 → 失敗' 1 'Only in committed: a.py'
regen fail
check 'regenerated：產生指令失敗 → 照它的結束碼失敗，不當成比對' 3 '產生器壞了' '與已提交的內容不同'

if [ -z "$(ls -A "$tmp/tmpdir")" ]; then ok 'regenerated：暫存的備份用完即刪'
else bad 'regenerated：暫存的備份沒有清掉' "$(ls -A "$tmp/tmpdir")"
fi

sh "$here/check-regenerated.sh" src > "$tmp/out" 2>&1; rc=$?
check 'regenerated：缺產生指令 → 結束碼 2' 2 '用法'

exit $fail
