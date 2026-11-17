---
version: alpha
name: EnvCite
description: 營運後台與客戶入口的企業介面：Google 雲端產品那一型（Material 3），淺色與深色兩套調色盤。一個品牌藍、一組帶藍的中性灰、一組色調容器、十五階字級、兩支自託管字型、Material 的狀態層與水波紋。
---

# EnvCite

執行期的值在 [DesignTokens.cs](DesignTokens.cs)。這裡是同一份給代理讀的視覺規格；改數值時兩份一起改。

## Overview

這是給人長時間核對法規的工作介面，外觀是 Google 雲端產品那一型（Material 3）：帶一點藍的淺灰頁面底，白色的卡片浮在上面；按鈕、側欄項目、搜尋列都是膠囊；選取用實心的淡藍色調容器；大字用常規字重，層次靠字級與面的色階，不靠粗體與色塊。冷靜、可預期、字要讀得完。淺色與深色兩套調色盤，角色完全相同：預設跟隨瀏覽器／系統的設定（`prefers-color-scheme`），使用者可以在登入後的個人設定手動選「亮色」或「暗色」覆寫，選擇記在這個瀏覽器。

企業色只有一支藍（產品自己的品牌藍，不是 Google 的藍；檔案版圖示對齊它）。填色按鈕與焦點環用同一支；長文連結深一階，讓小字在白底與淡底上都穩過 WCAG AA。中性灰取 Material 3 的 neutral 與 neutral-variant 色階（頁面底、表面、正文墨色、外框），狀態色只傳達成功、警示、錯誤與資訊，而且都是文字級，永遠搭配文字，不靠色相單獨分辨。裝飾用的青色（品牌圖形的葉色）不放文字。

回饋照 Material 3：游標移上去，元件上疊一層內容色的**狀態層**（8%）；按下時從落點擴散出**水波紋**，同時整顆輕縮到 0.97，放開再回到原尺寸、波紋淡出。動態是 Material 3 的強調曲線：沒有回彈。動態有四種來源，各有各的規矩：

- **回饋**：按下的當下縮放並長出水波紋，不等放開；懸停與聚焦是狀態層。
- **進出場**：選單從觸發點長出來；頁面的區塊進場時由上而下錯開一點點，淡入並上浮一小段，內容一到就開始，不讓人等。切換亮暗時，新的外觀從選的那一格以圓形展開到整頁。
- **載入**：用骨架屏畫出即將出現的版面（數字卡、卡片、表格），灰塊緩緩一明一暗，內容到了取代它；不用整頁轉圈或進度條。
- **手勢**：抽屜可以從螢幕左緣滑出、也可以往左滑收起。位置 1:1 跟著手指，放開時依速度投射到落點，再以放開當下的速度接續動畫，隨時可以重新抓住；只有手勢在邊界上有橡皮筋的阻力，回彈只屬於帶著動量的動作。

使用者要求減少動態時，位移與縮放歸零、不畫水波紋與圓形展開，只留淡入淡出，骨架屏停在靜止的灰塊。要求更高對比時，髮絲線加深到控制項外框、次要文字加深到正文。

## Colors

品牌藍寫一次：`primary` 與 `focus` 是同一色，`link` 是它的文字級。選取（側欄目前項、選中的分段、分頁數量）用實心的色調容器 `primary-container`，上面的字是 `on-primary-container`；它不用 `color-mix`，因為疊透明度會透出底下的表面，同一個選取在頂欄、側欄、卡片上就成了三個色。其餘的淡底、狀態層與淡邊都用 `color-mix` 疊在這些角色上，不另開一組色。

```yaml
colors:
  primary: "#005FC4"
  on-primary: "#FFFFFF"
  primary-container: "#D3E3FD"
  on-primary-container: "#041E49"
  link: "#004F9E"
  focus: "#005FC4"
  background: "#F8FAFD"
  surface: "#FFFFFF"
  surface-raised: "#FFFFFF"
  surface-muted: "#F0F4F9"
  text: "#1F1F1F"
  text-secondary: "#444746"
  ink: "#1F1F1F"
  on-ink: "#FFFFFF"
  accent: "#0D9488"
  success: "#146C2E"
  warning: "#8F4E00"
  error: "#B3261E"
  info: "#5E3BB5"
  border-subtle: "#E1E3E1"
  border-control: "#747775"
  scrim: "rgb(31 31 31 / 0.4)"
```

`surface-raised` 與 `surface` 在淺色同色：卡片不另鋪一層底，與頁面底的差別交給帶藍的 `background`。`surface-muted` 是 Material 3 的 surface-container：表頭、搜尋列、骨架屏與次要區塊。`ink` 是少量實心的強調底（頭像一類）：淺色主題是深塊，深色主題反過來是淺塊。

### 深色

深色是同一組角色的另一套值，不是另一套設計：元件只引用角色，不知道自己在哪一套。Material 3 的深色中性階（`#131314` 往上），層次靠表面一階比一階亮——背景最暗、卡片、浮起的面（對話框、選單）最亮——不靠陰影，深色底上陰影看不出來，所以陰影另有較重的一組。主色改用同一色相的亮階，實心按鈕上的字改深藍；色調容器反過來是深藍底配淺字；狀態色同理，取各自的亮階、維持文字級對比。每一組對比都由測試驗證，與淺色同一套標準（WCAG AA）。兩步驟驗證的 QR 固定白底黑碼，不隨主題變，驗證器 App 才掃得到。

```yaml
colors-dark:
  primary: "#A8C7FA"
  on-primary: "#062E6F"
  primary-container: "#004A77"
  on-primary-container: "#C2E7FF"
  link: "#A8C7FA"
  focus: "#A8C7FA"
  background: "#131314"
  surface: "#1E1F20"
  surface-raised: "#282A2C"
  surface-muted: "#333537"
  text: "#E3E3E3"
  text-secondary: "#C4C7C5"
  ink: "#E3E3E3"
  on-ink: "#131314"
  accent: "#2DD4BF"
  success: "#6DD58C"
  warning: "#FFB869"
  error: "#F2B8B5"
  info: "#D0BCFF"
  border-subtle: "#444746"
  border-control: "#8E918F"
  scrim: "rgb(0 0 0 / 0.6)"
```

## Typography

全站一支字型堆疊。拉丁字母與數字用 Google Sans Flex Variable（Google 產品介面的字，字重與光學尺寸兩軸：大字自動收緊、小字自動放寬）；中文（含法規罕用字）用 Noto Sans TC Variable（Google 的中文字型）。兩支都自己託管，各平台看到同一套字。`sans-serif` 只在字型檔還沒到的瞬間頂替。代碼、金鑰、識別碼與復原碼用等寬字 Roboto Mono Variable（`--ep-font-mono`，同樣自己託管）：每個字元同寬，0 有斜線，I、l、1 分得開，逐字核對不會看錯。表格與統計的數字用 `tabular-nums` 對齊。

字級是一張表、十五個角色：display、headline、title、body、label，各有 l、m、s。字重照 Material 3：展示與頁面標題是常規字重（Google 產品的大字不加粗），區塊標題、按鈕與標籤是 500。大字收緊字距、收緊行高；小字略放寬。內文至少 15px、行高至少 1.6；全站沒有小於 12px 的階。單位用 rem，瀏覽器字級放大時版面一起放大。`display-l` 與 `headline-l` 用 `clamp`，窄螢幕上不撐破版面。

```yaml
typography:
  display-l:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "clamp(2rem, 1.4rem + 2vw, 2.5rem)"
    fontWeight: 400
    lineHeight: "1.2"
    letterSpacing: "-0.02em"
  display-m:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "2rem"
    fontWeight: 400
    lineHeight: "1.2"
    letterSpacing: "-0.015em"
  display-s:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 400
    lineHeight: "1.25"
    letterSpacing: "-0.01em"
  headline-l:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "clamp(1.5rem, 1.25rem + 1vw, 1.75rem)"
    fontWeight: 400
    lineHeight: "1.3"
    letterSpacing: "-0.01em"
  headline-m:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 400
    lineHeight: "1.3"
    letterSpacing: "-0.005em"
  headline-s:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 500
    lineHeight: "1.4"
    letterSpacing: "0"
  title-l:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 500
    lineHeight: "1.45"
    letterSpacing: "0.005em"
  title-m:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: "1.5"
    letterSpacing: "0.01em"
  title-s:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 500
    lineHeight: "1.5"
    letterSpacing: "0.01em"
  body-l:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: "1.625"
    letterSpacing: "0.005em"
  body-m:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: "1.6"
    letterSpacing: "0.01em"
  body-s:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: "1.6"
    letterSpacing: "0.02em"
  label-l:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 500
    lineHeight: "1.3"
    letterSpacing: "0.01em"
  label-m:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: "1.4"
    letterSpacing: "0.02em"
  label-s:
    fontFamily: "Google Sans Flex Variable, Noto Sans TC Variable, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: "1.4"
    letterSpacing: "0.03em"
```

頁面標題用 `headline-l`，區塊與對話框標題用 `headline-s`，內文用 `body-l`，說明用 `body-m`，按鈕用 `label-l`，最小註記用 `label-s`。MudBlazor 的 H1–H6、Body、Caption、Button 都對到這張表，不另定字級。

## Layout

間距以 4px 為一階，欄寬用 rem。置中用 `margin-inline: auto` 或 `place-items: center`。可點的東西在觸控上至少 44×44（`2.75rem`）；客戶入口在滑鼠操作時收成緊湊高度（`control-height`，`2.25rem`）：側欄項目、輸入框工具列與頭像。版面三種：手機、平板 `48rem`、桌機 `80rem`。頂欄高 `64px`，與頁面同一個底（82% 疊模糊，內容從下面捲過去），在頂端時沒有分隔線，內容捲進來才浮出一條。

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

側欄是 Google 產品的導覽抽屜那一型：每項圖示加文字、整條膠囊，目前項是 `primary-container` 的實心淡藍底配 `on-primary-container` 的字與圖示，群組用小標題隔開，黏在頂欄下面、和頁面同一個底。客戶入口（`.ep-portal`）是對話產品那一型：側欄是比頁面高一階的平面加一條細線，目前項與最近的對話用同一個色調容器的膠囊，最近的對話比導覽淡一階，頭像用中性的淡底，頂欄矮到 `3.5rem`。已登入的外殼頂列是工具列：品牌只有文字字標，不放圖形（側欄頂列是字標加收合鈕，窄欄只剩收合鈕）；品牌圖形（一片葉沿葉脈裂成兩半）只在登入前與帳號頁、回答頭像、空白頁問候與分頁圖示出現。營運後台的頂欄左邊：導覽鈕，接著是產品名的文字字標（`headline-s`、常規字重、次要文字色，游標移上去才回主要文字色，點它回總覽），不帶「營運後台」之類的副標。頂欄中間的搜尋鈕是 Google 的搜尋列：整條膠囊（`pill` 圓角、沒有邊框、底色是帶一點主色的 `surface-muted`、左邊一個搜尋圖示、右邊是快速鍵提示），切換器開著時浮起成表面色加兩層陰影；手機收成只剩圖示的圓鈕。數字欄（`ColumnDef.Numeric`）標題與儲存格都靠右、等寬數字，數字後面的單位（次、tokens）占固定寬度，每一列的數字右緣才在同一條線上。後台的表格欄位可以排序（`ColumnDef.SortBy`）：標題是沒有框的按鈕，沒排時箭頭是淡的上下箭頭，排了換成朝上或朝下的箭頭與主要文字色，標題帶 `aria-sort`；依序切換升冪、降冪、回到原順序；沒有值的列永遠在最後。客戶入口的純表格是靜態的，不排序。桌機頂欄左邊的導覽鈕把它收成窄欄（偏好存在瀏覽器），手機上它是從左側滑出的抽屜、後面壓暗。滑入滑出走落定曲線，沒有回彈；減少動態時收成淡入淡出。

詳細頁（一個資源、多個面向，如租戶頁）是「固定的頭，加依主題分頁的身」：頁首、身分列與摘要磚留在上面，下面的分頁列（Material 3 的 primary tabs）一次只開一個主題（用量、API keys、成員、設定、稽核），每個分頁只做一件事，不超過七個。分頁是連結（`?tab=`），目前的那一個底下長出一道圓頭的主色線、字變主色，標籤旁可以帶數量（目前頁的數量是色調容器）；切換時新的面板淡入並上浮一小段，頁首與摘要不動。卡片並排一律用對稱格線（分頁裡的卡片、總覽頁的待辦與最近活動）：欄一樣寬、同一列一樣高，內容多的獨佔一整列，沒有「主欄加側欄」的 2:1；主要動作（新增、簽發）在頁首右側，附屬的動作（複製存取網址）貼在身分列右側、沒有外框，不另立一張卡。

窄表單不單獨放在滿寬卡片的左邊：兩個以上的小表單並排成對稱格線（目錄同步的設定與簽發）；欄位少的新增表單排成一列（新增成員）；單一任務的頁面（個人設定、綁定驗證器）把標題與卡片一起收在置中的窄欄（`.ep-narrow`，欄寬取問答欄），寬螢幕上兩側留白對稱。

表格一律是緊湊列高，沒有「緊湊」切換開關：這是給人長時間核對資料的工作介面，密度是設計決定，不是使用者的偏好；每多一個切換就多一種要驗證的外觀。

## Elevation & Depth

表面是平的：卡片是外框卡片（髮絲線、沒有陰影），層次靠帶藍的頁面底與白色的面。陰影只給浮起的東西，取 Material 3 的三層（Google 產品的灰藍陰影色）：懸停的實心按鈕一層，選單與聚焦的搜尋列兩層，對話框與抽屜三層。越大的面，陰影越遠。遮罩是 `scrim`。對話框背後壓暗；選單與頂欄只用半透明與偏移，不鋪整頁遮罩，閱讀可以繼續。

## Shapes

Material 3 的形狀階：控制項（輸入框、選單項）8px，卡片與選單 16px，登入卡、對話框與抽屜 28px，按鈕、晶片、分段、側欄項目、搜尋列、徽章全圓。同一畫面裡圓角只從這四階取。

```yaml
rounded:
  control: 8px
  card: 16px
  sheet: 28px
  pill: 9999px
```

## Components

按下即回饋（狀態層、水波紋、輕縮），焦點環一律 2px、偏移 2px，顏色是 `focus`；輸入框例外：聚焦時外框本身變成 2px 的 `focus`（Material 3 的外框欄位），焦點環收成透明（強制色彩模式下仍會畫出系統色）。主要動作是實心膠囊（平放沒有陰影，游標移上去浮起一層並疊上 `on-primary` 的狀態層）；次要動作是外框膠囊：連結色的字加控制項外框，游標移上去疊一層連結色的狀態層。MudBlazor 的按鈕、對話框與提示框照同一套形狀與陰影。

水波紋由 `forms.js` 給落點、半徑與狀態（`data-ripple`），樣式表只畫一層與元件同形狀的面，用 `clip-path` 的圓從落點長大，顏色是元件的內容色、不透明度是狀態層的按下值，所以在任何底色、淺色與深色都成立；不裁切元件本身，分頁的主色線與焦點環都不受影響。

設定或修改密碼的欄位用 `NewPasswordField`：欄位下方一份三條的短清單（長度、不含 email、避開常見密碼），前面是記號——靜態的是圓點，長度那條在有 JavaScript 時是空心圈，夠長變打勾與完成色（形狀也變，不只靠顏色），並在右側說還差幾個；清單只動文字色與記號，不動版面，減少動態時顏色轉換收成瞬間。密碼欄一律用 `PasswordField`：輸入框內側有顯示／隱藏的眼睛鈕，大寫鎖定開著時欄位下方提醒；沒有 JavaScript 時鈕不出現，欄位仍是普通的密碼欄。一次性的復原碼一律用 `RecoveryCodes`：標題列放下載（文字檔）、複製、列印，窄的框只留字。登入與帳號卡裡，主要動作與登出整列滿寬。

```yaml
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-l}"
    rounded: "{rounded.pill}"
    padding: 0.5rem 1.5rem
  button-quiet:
    backgroundColor: transparent
    textColor: "{colors.link}"
    typography: "{typography.label-l}"
    rounded: "{rounded.pill}"
    padding: 0.5rem 1.5rem
  nav-item-active:
    backgroundColor: "{colors.primary-container}"
    textColor: "{colors.on-primary-container}"
    typography: "{typography.label-l}"
    rounded: "{rounded.pill}"
    padding: 0.5rem 1rem
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
  dialog:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    typography: "{typography.headline-s}"
    rounded: "{rounded.sheet}"
    padding: 1.5rem
```

## Do's and Don'ts

- 企業藍只當主要動作、焦點與連結；連結用 `link`，不要把填色按鈕的 `primary` 直接當長文的字色
- 選取一律用 `primary-container`／`on-primary-container`，不用主色疊透明度
- 懸停與按下用狀態層（`--ep-state-*` 的不透明度疊內容色），不另挑一個「深一點的色」
- 狀態色旁邊要有文字；`accent` 只做裝飾
- 大字用常規字重，層次靠字級；字級從十五階裡挑，字距與行高跟該階走，不要另寫一個 px 字級
- 中文與拉丁共用上面那一支字型堆疊
- 新的顏色、字級、圓角、間距、動態寫進 `DesignTokens.cs` 與這份規格
- 樣式表引用 `--ep-*`，不寫色碼、字重、時長、緩動與觸控尺寸；腳本畫的動畫（水波紋、圓形展開）也從 token 讀時長與曲線
- 新增的元件在淺色與深色都要看過；顏色只用角色，不寫死，QR 這類必須固定的例外要寫明原因
- 減少動態時用淡入淡出，不留位移、縮放、水波紋與圓形展開
- 不可逆的動作先確認；新的 API key 與一次性密碼只顯示一次
