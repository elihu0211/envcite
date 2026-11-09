#!/bin/sh
# check-proto.sh、check-openapi.sh 的離線測試：暫時 repo 當 origin，假的 buf／oasdiff 記錄被呼叫的
# 參數，不連網。
#   sh ci/test-check-scripts.sh
# 每個情境斷言「結束碼」與「工具實際被呼叫的參數」——後者才是「跳過」與「失敗不被吞掉」的證據。
set -u
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

mkdir "$tmp/bin"
# buf：記錄參數；FAKE_BUF_FAIL 指定哪個子指令失敗。
cat > "$tmp/bin/buf" <<'FAKE'
#!/bin/sh
echo "buf $*" >> "$CALLS"
[ "${FAKE_BUF_FAIL:-}" = "$1" ] && exit 1
exit 0
FAKE
# oasdiff：記錄參數與「基準規格檔的內容」（證明讀的是基準分支的版本，不是工作樹的）。
cat > "$tmp/bin/oasdiff" <<'FAKE'
#!/bin/sh
shift
baseline=$1
shift
echo "oasdiff breaking BASE=$(cat "$baseline") $*" >> "$CALLS"
[ "${FAKE_OASDIFF_FAIL:-}" = 1 ] && exit 1
exit 0
FAKE
chmod +x "$tmp/bin/buf" "$tmp/bin/oasdiff"

# origin 的 main 是基準；work 是 clone，工作樹把規格改成 v2。
mkrepos() { # with | without：基準分支有沒有 buf.yaml 與規格
  rm -rf "$tmp/origin" "$tmp/work"
  git init -q -b main "$tmp/origin"
  if [ "$1" = with ]; then
    echo 'version: v2' > "$tmp/origin/buf.yaml"
    mkdir "$tmp/origin/api"
    printf v1 > "$tmp/origin/api/spec.json"
  else
    echo x > "$tmp/origin/README"
  fi
  git -C "$tmp/origin" add -A
  git -C "$tmp/origin" commit -q -m base
  git clone -q "$tmp/origin" "$tmp/work"
  mkdir -p "$tmp/work/api"
  printf v2 > "$tmp/work/api/spec.json"
  sha=$(git -C "$tmp/origin" rev-parse main)
}

run_case() { # 名稱 預期結束碼（數字或 nonzero） 預期呼叫記錄 輸出須含的字樣（可空） 腳本 [參數...]
  name=$1 want_rc=$2 want_calls=$3 want_out=$4 script=$5
  shift 5
  : > "$tmp/calls"
  out=$(cd "$tmp/work" && PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" sh "$here/$script" "$@" 2>&1)
  rc=$?
  got=$(cat "$tmp/calls")
  ok=1
  if [ "$want_rc" = nonzero ]; then
    [ "$rc" != 0 ] || ok=0
  else
    [ "$rc" = "$want_rc" ] || ok=0
  fi
  [ "$got" = "$want_calls" ] || ok=0
  case "$out" in *"$want_out"*) ;; *) ok=0 ;; esac
  if [ "$ok" = 1 ]; then
    echo "ok   $name"
  else
    echo "FAIL $name：結束碼 $rc（預期 $want_rc）、呼叫「$got」（預期「$want_calls」）、輸出「$out」"
    fail=1
  fi
}

nl='
'

mkrepos with
run_case 'proto：基準有 buf.yaml → lint 後以基準 commit 比對' 0 \
  "buf lint${nl}buf breaking --against .git#ref=$sha" '' check-proto.sh main
run_case 'proto：基準分支不存在 → 取回失敗、不比對' nonzero 'buf lint' '' check-proto.sh nope
run_case 'proto：沒給基準 → 用法錯誤、什麼都不跑' 2 '' '用法' check-proto.sh
FAKE_BUF_FAIL=breaking; export FAKE_BUF_FAIL
run_case 'proto：breaking 失敗 → 結束碼 1 原樣傳出' 1 \
  "buf lint${nl}buf breaking --against .git#ref=$sha" '' check-proto.sh main
FAKE_BUF_FAIL=lint
run_case 'proto：lint 失敗 → 立刻紅、不取回基準、不比對' 1 'buf lint' '' check-proto.sh main
unset FAKE_BUF_FAIL

mkrepos without
run_case 'proto：基準尚無 buf.yaml → 明示跳過、不比對' 0 'buf lint' '跳過' check-proto.sh main

mkrepos with
run_case 'openapi：基準有規格 → 讀基準版本、預設 --fail-on ERR' 0 \
  'oasdiff breaking BASE=v1 api/spec.json --fail-on ERR' '' check-openapi.sh main api/spec.json
run_case 'openapi：--allow-breaking → 不帶 --fail-on' 0 \
  'oasdiff breaking BASE=v1 api/spec.json' '' check-openapi.sh --allow-breaking main api/spec.json
run_case 'openapi：基準分支不存在 → 取回失敗、不比對' nonzero '' '' check-openapi.sh nope api/spec.json
run_case 'openapi：參數不足 → 用法錯誤' 2 '' '用法' check-openapi.sh main
FAKE_OASDIFF_FAIL=1; export FAKE_OASDIFF_FAIL
run_case 'openapi：有破壞 → 結束碼 1 原樣傳出' 1 \
  'oasdiff breaking BASE=v1 api/spec.json --fail-on ERR' '' check-openapi.sh main api/spec.json
unset FAKE_OASDIFF_FAIL

mkrepos without
run_case 'openapi：基準尚無規格 → 明示跳過、不比對' 0 '' '跳過' check-openapi.sh main api/spec.json

exit $fail
