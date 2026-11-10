#!/bin/sh
# 依兩個 git 修訂之間的路徑差異，寫出哪些 CI job 該跑。
#
# 用法：sh ci/detect-changes.sh <base-sha> <事件 pull_request|push> <輸出檔>
#
# 輸出檔每行 key=true 或 key=false。檢查只在 pull_request 為 true；依賴與映像在 push 上也依路徑為 true。
# 改 workflow 或建置腳本時，pull_request 會把相關 job 打開；push 到預設分支不會因此重建映像。
# 不讀 CI 平台的變數。base 為 40 個 0 時視為全部都變了。
set -eu

base=${1:?}
event=${2:?}
out=${3:?}

all=0
case $base in
  '' | 0000000000000000000000000000000000000000) all=1 ;;
esac

if [ "$all" = 1 ]; then
  files=$(git ls-files)
else
  files=$(git diff --name-only "$base" HEAD)
fi

has() { printf '%s\n' "$files" | grep -Eq "$1"; }

pr=0
[ "$event" = pull_request ] && pr=1

ci=0
if [ "$pr" = 1 ] && has '^\.github/(workflows|actions)/|^ci/(build-image|restore-dotnet|detect-changes)\.sh$'; then
  ci=1
fi

# 1 if the regex matches or this pull request changed the trigger layer.
want() {
  if has "$1" || [ "$ci" = 1 ]; then
    echo 1
  else
    echo 0
  fi
}

yn() { if [ "$1" = 1 ]; then echo true; else echo false; fi; }

platform=$(want '^(services/platform/(src|tests|openapi)/|services/platform/(\.editorconfig|Dockerfile|EnvCite\.Platform\.slnx|dotnet-tools\.json)|libs/dotnet/|global\.json|Directory\.|\.editorconfig|proto/|ci/check-no-changes\.sh)')
consult=$(want '^(services/consult/(src|tests)/|services/consult/(Dockerfile|EnvCite\.Consult\.slnx)|libs/dotnet/|global\.json|Directory\.|\.editorconfig|proto/)')
dotnet=$(want '^(services/platform/(src|tests|openapi)/|services/platform/(\.editorconfig|Dockerfile|EnvCite\.Platform\.slnx|dotnet-tools\.json)|services/consult/(src|tests)/|services/consult/(Dockerfile|EnvCite\.Consult\.slnx)|libs/dotnet/|global\.json|Directory\.|\.editorconfig|proto/|ci/(restore-dotnet|check-no-changes)\.sh)')
python=$(want '^(services/ontology/|libs/python/|proto/|pyproject\.toml|uv\.lock|ci/check-regenerated\.sh)')
segmenter=$(want '^(libs/python/envcite-segmenter/(src|tests|scripts)/|libs/python/envcite-segmenter/pyproject\.toml|pyproject\.toml|uv\.lock)')
jobkit=$(want '^(libs/python/envcite-jobkit/(src|tests)/|libs/python/envcite-jobkit/pyproject\.toml|pyproject\.toml|uv\.lock)')
python_proto=$(want '^(libs/python/envcite-proto/(src|scripts)/|libs/python/envcite-proto/pyproject\.toml|proto/|pyproject\.toml|uv\.lock|ci/check-regenerated\.sh)')
proto=$(want '^(proto/|buf\.yaml|ci/check-proto\.sh)')
dockerfile=$(want '^services/[^/]+/Dockerfile')
openapi=$platform
if [ "$(want '^ci/check-openapi\.sh')" = 1 ]; then openapi=1; fi
ontology_image=$python
platform_image=$platform
consult_image=$consult

if [ "$pr" != 1 ]; then
  platform=0 consult=0 segmenter=0 jobkit=0 python_proto=0 proto=0 dockerfile=0 openapi=0
fi

docs=0 renovate=0 ci_scripts=0
if [ "$pr" = 1 ]; then
  has '(^|/)[^/]+\.md$|^ci/check-docs\.py$' && docs=1 || docs=0
  has '^renovate\.json$' && renovate=1 || renovate=0
  has '^ci/.*\.sh$|^\.githooks/|^\.github/(workflows|actions)/' && ci_scripts=1 || ci_scripts=0
fi

{
  printf 'platform=%s\n' "$(yn "$platform")"
  printf 'consult=%s\n' "$(yn "$consult")"
  printf 'dotnet=%s\n' "$(yn "$dotnet")"
  printf 'python=%s\n' "$(yn "$python")"
  printf 'segmenter=%s\n' "$(yn "$segmenter")"
  printf 'jobkit=%s\n' "$(yn "$jobkit")"
  printf 'python_proto=%s\n' "$(yn "$python_proto")"
  printf 'proto=%s\n' "$(yn "$proto")"
  printf 'dockerfile=%s\n' "$(yn "$dockerfile")"
  printf 'openapi=%s\n' "$(yn "$openapi")"
  printf 'docs=%s\n' "$(yn "$docs")"
  printf 'renovate=%s\n' "$(yn "$renovate")"
  printf 'ci_scripts=%s\n' "$(yn "$ci_scripts")"
  printf 'ontology_image=%s\n' "$(yn "$ontology_image")"
  printf 'consult_image=%s\n' "$(yn "$consult_image")"
  printf 'platform_image=%s\n' "$(yn "$platform_image")"
} > "$out"
