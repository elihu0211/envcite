---
version: alpha
name: EnvCite
description: 營運後台與客戶入口的淺色企業介面。一個品牌藍、一組中性灰、十五階字級、兩支自託管字型。
---

# EnvCite

執行期的值在 [DesignTokens.cs](DesignTokens.cs)。這裡是同一份給代理讀的視覺規格；改數值時兩份一起改。

## Overview

這是給人長時間核對法規的工作介面：冷靜、可預期、字要讀得完。層次靠留白、髮絲線與少量陰影，不靠色塊，也不做深色主題。

企業色只有一支藍。填色按鈕與焦點環用同一支；長文連結深一階，讓小字在白底上達到 WCAG AA。中性灰取系統介面那一組（頁面底、正文墨色），狀態色只傳達成功、警示、錯誤與資訊，而且都是文字級，永遠搭配文字，不靠色相單獨分辨。裝飾用的青色不放文字。

按下的當下就縮到 0.97，放開再回到原尺寸。動態是臨界阻尼：沒有回彈。這個介面沒有拖曳，回彈沒有動量可以承接。使用者要求減少動態時，位移與縮放歸零，只留淡入淡出。要求更高對比時，髮絲線加深到控制項外框、次要文字加深到正文。

## Colors

品牌藍寫一次：`primary` 與 `focus` 是同一色，`link` 是它的文字級。淡底與淡邊用 `color-mix` 疊在這些角色上，不另開一組色。

```yaml
colors:
  primary: "#005FC4"
  on-primary: "#FFFFFF"
  link: "#004F9E"
  focus: "#005FC4"
  background: "#F5F5F7"
  surface: "#FFFFFF"
  surface-raised: "#FFFFFF"
  surface-muted: "#F0F0F3"
  text: "#1D1D1F"
  text-secondary: "#55555B"
  ink: "#1D1D1F"
  on-ink: "#FFFFFF"
  accent: "#14B8A6"
  success: "#0B7857"
  warning: "#AC5107"
  error: "#AC312C"
  info: "#1D52AF"
  border-subtle: "#E3E3E8"
  border-control: "#86868B"
  scrim: "rgb(15 23 42 / 0.5)"
```

`surface-raised` 與 `surface` 同色：卡片不另鋪一層底，厚度交給陰影。`ink` 是淺色頁面上的少量實心深色（頭像一類），不是深色主題。

## Typography

全站一支字型堆疊。拉丁字母、數字與光學尺寸用 Inter Variable；中文（含法規罕用字）用 Noto Sans TC Variable。兩支都自己託管，各平台看到同一套字。`sans-serif` 只在字型檔還沒到的瞬間頂替。代碼、金鑰與識別碼不換等寬字，沿用 Inter，打開 `ss02`、`zero`、`tnum`，讓 0、I、l、1 分得開。

字級是一張表、十五個角色：display、headline、title、body、label，各有 l、m、s。大字收緊字距、收緊行高；小字略放寬。內文至少 15px、行高至少 1.6；全站沒有小於 12px 的階。單位用 rem，瀏覽器字級放大時版面一起放大。`display-l` 與 `headline-l` 用 `clamp`，窄螢幕上不撐破版面。

```yaml
typography:
  display-l:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "clamp(2rem, 1.4rem + 2vw, 2.25rem)"
    fontWeight: 700
    lineHeight: "1.15"
    letterSpacing: "-0.02em"
  display-m:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: "1.15"
    letterSpacing: "-0.02em"
  display-s:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: "1.2"
    letterSpacing: "-0.015em"
  headline-l:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "clamp(1.5rem, 1.25rem + 1vw, 1.75rem)"
    fontWeight: 700
    lineHeight: "1.25"
    letterSpacing: "-0.015em"
  headline-m:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: "1.3"
    letterSpacing: "-0.01em"
  headline-s:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: "1.4"
    letterSpacing: "-0.005em"
  title-l:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: "1.45"
    letterSpacing: "0"
  title-m:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: "1.5"
    letterSpacing: "0"
  title-s:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: "1.5"
    letterSpacing: "0.005em"
  body-l:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: "1.625"
    letterSpacing: "0"
  body-m:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: "1.6"
    letterSpacing: "0.005em"
  body-s:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: "1.6"
    letterSpacing: "0.01em"
  label-l:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: "1.3"
    letterSpacing: "0.005em"
  label-m:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: "1.4"
    letterSpacing: "0.01em"
  label-s:
    fontFamily: "Inter Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: "1.4"
    letterSpacing: "0.015em"
```

頁面標題用 `headline-l`，區塊標題用 `headline-s`，內文用 `body-l`，說明用 `body-m`，按鈕用 `label-l`，最小註記用 `label-s`。MudBlazor 的 H1–H6、Body、Caption、Button 都對到這張表，不另定字級。

## Layout

間距以 4px 為一階，欄寬用 rem。置中用 `margin-inline: auto` 或 `place-items: center`。可點的東西在觸控上至少 44×44（`2.75rem`）。版面三種：手機、平板 `48rem`、桌機 `80rem`。頂欄高 `64px`，材質是表面色的 72% 疊模糊，內容從下面捲過去。

```yaml
spacing:
  "1": 0.25rem
  "2": 0.5rem
  "3": 0.75rem
  "4": 1rem
  "6": 1.5rem
  "8": 2rem
  "12": 3rem
```

窄欄 `28rem` 給登入與表單，問答欄 `40rem`（桌機 `46rem`），入口主欄 `60rem`，營運後台 `76rem`，側欄 `15rem`（收成只剩圖示的窄欄時 `4.5rem`）。

側欄是雲端主控台那一型：每項圖示加文字，目前項是整條的淡藍底，群組用小標題隔開，黏在頂欄下面、和頁面同一個底。桌機頂欄左邊的導覽鈕把它收成窄欄（偏好存在瀏覽器），手機上它是從左側滑出的抽屜、後面壓暗。滑入滑出走落定曲線，沒有回彈；減少動態時收成淡入淡出。

## Elevation & Depth

表面是平的。小控制項一層薄陰影，登入卡與抽屜再厚一層，對話框最厚。越大的面，陰影越遠。遮罩是 `scrim`。對話框背後壓暗；選單與頂欄只用半透明與偏移，不鋪整頁遮罩，閱讀可以繼續。

## Shapes

控制項 8px，卡片 12px，登入卡與抽屜 16px，藥丸全圓。同一畫面裡圓角只從這四階取。

```yaml
rounded:
  control: 8px
  card: 12px
  sheet: 16px
  pill: 9999px
```

## Components

按下即回饋，焦點環一律 2px、偏移 2px，顏色是 `focus`。主要動作實心，次要動作是連結色的文字加控制項外框。

密碼欄一律用 `PasswordField`：輸入框內側有顯示／隱藏的眼睛鈕，大寫鎖定開著時欄位下方提醒；沒有 JavaScript 時鈕不出現，欄位仍是普通的密碼欄。一次性的復原碼一律用 `RecoveryCodes`：標題列放下載（文字檔）、複製、列印，窄的框只留字。登入與帳號卡裡，主要動作與登出整列滿寬。

```yaml
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-l}"
    rounded: "{rounded.control}"
    padding: 0.5rem
  button-quiet:
    backgroundColor: transparent
    textColor: "{colors.link}"
    typography: "{typography.label-l}"
    rounded: "{rounded.control}"
    padding: 0.5rem
  text-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-l}"
    rounded: "{rounded.control}"
    padding: 0.75rem
  chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-m}"
    rounded: "{rounded.pill}"
    padding: 0.5rem
```

## Do's and Don'ts

- 企業藍只當主要動作、焦點與連結；連結用 `link`，不要把填色按鈕的 `primary` 直接當長文的字色
- 狀態色旁邊要有文字；`accent` 只做裝飾
- 字級從十五階裡挑，字距與行高跟該階走，不要另寫一個 px 字級
- 中文與拉丁共用上面那一支字型堆疊
- 新的顏色、字級、圓角、間距寫進 `DesignTokens.cs` 與這份規格
- 樣式表引用 `--ep-*`，不寫色碼、字重、時長、緩動與觸控尺寸
- 保持淺色。減少動態時用淡入淡出，不留位移與縮放
- 不可逆的動作先確認；新的 API key 與一次性密碼只顯示一次
