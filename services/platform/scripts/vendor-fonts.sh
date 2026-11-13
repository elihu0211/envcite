#!/usr/bin/env bash
# 把後台字型（Roboto、Roboto Mono、Noto Sans TC，皆 OFL）從 Fontsource 的 npm 套件取出，放進 wwwroot/fonts/ 自己託管：
# 不對第三方 CDN 發請求，也不必讓 .NET 專案依賴 npm。升級時改下面三個版本，從服務目錄執行本腳本後一起提交。
#
# 中文字型依 unicode-range 切成上百片，瀏覽器只下載頁面上出現的字所在的那幾片。
# Roboto 排在字型清單第一，所以它的拉丁子集要讓出中文排版用的標點（引號、刪節號、破折號、間隔號），
# 那幾個字元才會落到 Noto Sans TC，排成全形；否則中文句子裡的引號是 Roboto 的半形字形。
# Roboto Mono 只給代碼、金鑰、識別碼（逐字核對的字串），裡面沒有中文標點，不用讓出。
set -euo pipefail

ROBOTO_VERSION=5.3.0
ROBOTO_MONO_VERSION=5.3.0
NOTO_VERSION=5.3.1

cd "$(dirname "$0")/.."
out=src/EnvCite.Platform.Web/wwwroot/fonts
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

(cd "$work" && npm pack --silent \
    "@fontsource-variable/roboto@$ROBOTO_VERSION" \
    "@fontsource-variable/roboto-mono@$ROBOTO_MONO_VERSION" \
    "@fontsource-variable/noto-sans-tc@$NOTO_VERSION" >/dev/null)
mkdir -p "$work/roboto" "$work/roboto-mono" "$work/noto"
tar -xzf "$work/fontsource-variable-roboto-$ROBOTO_VERSION.tgz" -C "$work/roboto"
tar -xzf "$work/fontsource-variable-roboto-mono-$ROBOTO_MONO_VERSION.tgz" -C "$work/roboto-mono"
tar -xzf "$work/fontsource-variable-noto-sans-tc-$NOTO_VERSION.tgz" -C "$work/noto"

rm -rf "$out"
mkdir -p "$out/roboto" "$out/roboto-mono" "$out/noto-sans-tc"
cp "$work/roboto/package/LICENSE" "$out/roboto/LICENSE"
cp "$work/roboto-mono/package/LICENSE" "$out/roboto-mono/LICENSE"
cp "$work/noto/package/LICENSE" "$out/noto-sans-tc/LICENSE"

# 以空行分段：每段是一條 @font-face。Roboto 與 Roboto Mono 取拉丁與擴充拉丁（wght 版，只帶字重軸）；
# Noto 取中文的編號切片與拉丁（給從 Roboto 讓出的標點），西里爾與越南文不需要。
pick() { awk -v RS= -v ORS='\n\n' -v keep="$2" '$0 ~ keep' "$1"; }
{
    pick "$work/roboto/package/wght.css" '/roboto-latin(-ext)?-wght-normal[.]woff2' \
        | sed -e 's#url(./files/#url(roboto/#' \
              -e '/roboto-latin-wght-normal.woff2/,/unicode-range/ s#U+0000-00FF#U+0000-00B6,U+00B8-00FF#' \
              -e '/roboto-latin-wght-normal.woff2/,/unicode-range/ s#U+2000-206F#U+2000-2013,U+2015-2017,U+201E-2025,U+2027-206F#'
    pick "$work/roboto-mono/package/wght.css" '/roboto-mono-latin(-ext)?-wght-normal[.]woff2' \
        | sed -e 's#url(./files/#url(roboto-mono/#'
    pick "$work/noto/package/wght.css" '/noto-sans-tc-([0-9]+|latin)-wght-normal[.]woff2' \
        | sed -e 's#url(./files/#url(noto-sans-tc/#'
} >"$out/fonts.css"

grep -o 'url([^)]*)' "$out/fonts.css" | sed -e 's#url(##' -e 's#)##' | while read -r file; do
    case "$file" in
        roboto/*) cp "$work/roboto/package/files/${file#roboto/}" "$out/$file" ;;
        roboto-mono/*) cp "$work/roboto-mono/package/files/${file#roboto-mono/}" "$out/$file" ;;
        noto-sans-tc/*) cp "$work/noto/package/files/${file#noto-sans-tc/}" "$out/$file" ;;
    esac
done

echo "fonts: $(grep -c '@font-face' "$out/fonts.css") faces, $(du -sh "$out" | cut -f1)"
