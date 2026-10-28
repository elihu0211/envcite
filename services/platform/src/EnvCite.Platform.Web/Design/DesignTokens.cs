namespace EnvCite.Platform.Web.Design;

/// <summary>
/// 一組配色。語意名稱而非色名：元件只說「這是主要文字」「這是危險」，換主題只換這張表。
/// 文字配對的對比度由 <c>DesignTokenTests</c> 驗證（WCAG AA：一般文字 4.5:1、控制項外框 3:1）。
/// 一個角色一個色：淡底與邊框由 CSS 以 <c>color-mix</c> 疊透明度取得，不另開 -bg／-border 的色。
/// </summary>
public sealed record ColorTokens
{
    public required string Background { get; init; }

    public required string Surface { get; init; }

    public required string SurfaceRaised { get; init; }

    /// <summary>表格欄標題、hover 列、次要區塊的底。</summary>
    public required string SurfaceMuted { get; init; }

    public required string Text { get; init; }

    public required string TextSecondary { get; init; }

    public required string Primary { get; init; }

    /// <summary>深色層次：頭像等少量實心深色元素。不是深色主題，只是淺色頁面裡的深色點綴。</summary>
    public required string Ink { get; init; }

    /// <summary>放在 <see cref="Ink"/> 上的文字。</summary>
    public required string OnInk { get; init; }

    /// <summary>第二個強調色，只做裝飾，不承載文字，所以不受文字對比限制。</summary>
    public required string Accent { get; init; }

    /// <summary>放在 <see cref="Primary"/> 上的文字。</summary>
    public required string OnPrimary { get; init; }

    /// <summary>文字連結的顏色。與 <see cref="Primary"/>（按鈕等填色）分開，主題可以各自調整。</summary>
    public required string Link { get; init; }

    public required string Success { get; init; }

    public required string Warning { get; init; }

    public required string Error { get; init; }

    public required string Info { get; init; }

    /// <summary>裝飾性的分隔線與卡片邊框；不承載資訊，不受非文字對比限制。</summary>
    public required string BorderSubtle { get; init; }

    /// <summary>輸入框等控制項的外框；要讓人認得出控制項在哪裡，所以對表面至少 3:1。</summary>
    public required string BorderControl { get; init; }

    /// <summary>鍵盤焦點環。</summary>
    public required string Focus { get; init; }

    /// <summary>對話框與抽屜後面的遮罩。</summary>
    public required string Scrim { get; init; }
}

public static class DesignTokens
{
    // 中性灰階（近乎無彩度）加單一藍色強調；表面以平面為主，層次靠細邊與淡陰影，不靠色塊。全站沒有漸層。
    // 狀態色全在 sRGB 色域內、彩度有上限（狀態要比品牌色顯眼，但不能搶過它），而且是文字級：
    // 對白底與一成淡底（徽章、提示框的字）都 ≥ 4.5:1。警示與危險兩色在這個明度本來就近，所以永遠搭配圖示與文字，不靠色相分辨。
    // 換主題只換這張表：語意名稱不變，DesignTokenTests 會重新驗證每一組對比。
    // 企業色只寫一次。主色與焦點環指向它；連結是同一色的文字級（填色按鈕太亮，長文要深一階才過 4.5:1）。
    public const string Brand = "#005FC4";

    public const string BrandText = "#004F9E";

    public static ColorTokens Light { get; } = new()
    {
        Background = "#F5F5F7",
        Surface = "#FFFFFF",
        SurfaceRaised = "#FFFFFF",
        SurfaceMuted = "#F0F0F3",
        Text = "#1D1D1F",
        TextSecondary = "#55555B",
        Primary = Brand,
        Ink = "#1D1D1F",
        OnInk = "#FFFFFF",
        Accent = "#14B8A6",
        OnPrimary = "#FFFFFF",
        Link = BrandText,
        Success = "#0B7857",
        Warning = "#AC5107",
        Error = "#AC312C",
        Info = "#1D52AF",
        BorderSubtle = "#E3E3E8",
        BorderControl = "#86868B",
        Focus = Brand,
        Scrim = "rgb(15 23 42 / 0.5)",
    };

    // ---- 字型 ----------------------------------------------------------------------------------
    // 預設用平台系統字型：已內建光學尺寸與字距調校，也不對第三方發請求，也不必隨程式帶字型檔。
    // 繁中字型依平台列出，最後退到 sans-serif。

    public static string[] FontFamily { get; } =
    [
        "system-ui", "-apple-system", "Segoe UI", "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", "sans-serif",
    ];

    public static string[] FontFamilyMono { get; } =
        ["ui-monospace", "SF Mono", "Menlo", "Consolas", "monospace"];

    /// <summary>
    /// 字級階層：字距與行高隨字級變化（大字收緊、小字略放寬），不是一個固定值。
    /// 內文層（小字、說明）比常見的標準值各大 1px，公務與法規的長時間閱讀比較不累；
    /// 中文內文行高取 1.6 以上。單位 rem，使用者調整瀏覽器字級時版面跟著放大。
    /// </summary>
    public sealed record TypeStyle(string Size, string LineHeight, string LetterSpacing, int Weight);

    public static TypeStyle Display { get; } = new("clamp(2rem, 1.4rem + 2vw, 2.25rem)", "1.15", "-0.02em", 700);

    public static TypeStyle H1 { get; } = new("clamp(1.5rem, 1.25rem + 1vw, 1.75rem)", "1.25", "-0.015em", 700);

    public static TypeStyle H2 { get; } = new("1.5rem", "1.3", "-0.01em", 600);

    public static TypeStyle H3 { get; } = new("1.25rem", "1.4", "-0.005em", 600);

    public static TypeStyle Body { get; } = new("1rem", "1.625", "0", 400);

    public static TypeStyle BodySmall { get; } = new("0.9375rem", "1.6", "0.005em", 400);

    public static TypeStyle Caption { get; } = new("0.8125rem", "1.45", "0.01em", 400);

    /// <summary>全站最小字級，沒有更小的階：徽章、表格註記。</summary>
    public static TypeStyle Micro { get; } = new("0.75rem", "1.4", "0.015em", 500);

    public static TypeStyle Button { get; } = new("0.9375rem", "1.3", "0.005em", 600);

    // ---- 間距、圓角、陰影、動態 -----------------------------------------------------------------

    /// <summary>4px 為單位的間距階。</summary>
    public static IReadOnlyDictionary<string, string> Space { get; } = new Dictionary<string, string>
    {
        ["1"] = "0.25rem",
        ["2"] = "0.5rem",
        ["3"] = "0.75rem",
        ["4"] = "1rem",
        ["6"] = "1.5rem",
        ["8"] = "2rem",
        ["12"] = "3rem",
    };

    /// <summary>
    /// 置中欄寬，不是間距階。窄欄是登入與表單，問答欄在桌機再寬一階，頁面主欄是入口內容。
    /// 單位 rem，字級放大時欄一起變寬。置中用樣式表的 <c>margin-inline: auto</c> 或 <c>place-items: center</c>，不另寫置中函式。
    /// </summary>
    public static IReadOnlyDictionary<string, string> Measure { get; } = new Dictionary<string, string>
    {
        ["narrow"] = "28rem",
        ["ask"] = "40rem",
        ["ask-desktop"] = "46rem",
        ["page"] = "60rem",
        // 營運後台：表格與兩欄的設定頁要寬，但超過這個寬度的一行就讀不完了。
        ["console"] = "76rem",
        ["sidebar"] = "15rem",
    };

    public static IReadOnlyDictionary<string, string> Radius { get; } = new Dictionary<string, string>
    {
        ["control"] = "8px",
        ["card"] = "12px",
        ["sheet"] = "16px",
        ["pill"] = "9999px",
    };

    /// <summary>越大的面越厚：陰影與模糊都隨之加重。</summary>
    public static IReadOnlyDictionary<string, string> Elevation { get; } = new Dictionary<string, string>
    {
        ["1"] = "0 1px 2px rgb(15 23 42 / 0.06), 0 1px 3px rgb(15 23 42 / 0.1)",
        ["2"] = "0 4px 8px -2px rgb(15 23 42 / 0.1), 0 2px 4px -2px rgb(15 23 42 / 0.06)",
        ["3"] = "0 12px 16px -4px rgb(15 23 42 / 0.12), 0 4px 6px -2px rgb(15 23 42 / 0.05)",
    };

    /// <summary>
    /// 動態：時長依互動類別命名，不依毫秒數（fast 狀態回饋、normal 元件進出、slow 大面積位移、
    /// reveal 內容揭露）；緩動依「運動的角色」命名，與時長正交（時長講多久，曲線講怎麼動）。
    /// enter 快起慢收（眼睛在落點停得住）、exit 慢起快收（離場不搶戲）、move 兩端都緩（已在畫面上的東西換位置）。
    /// 臨界阻尼，沒有回彈：回彈只屬於帶著動量的手勢，這個介面沒有拖曳。
    /// </summary>
    public static IReadOnlyDictionary<string, string> Motion { get; } = new Dictionary<string, string>
    {
        ["fast"] = "150ms",
        ["normal"] = "200ms",
        ["slow"] = "300ms",
        ["reveal"] = "500ms",
        ["ease-standard"] = "cubic-bezier(0.4, 0, 0.2, 1)",
        ["ease-enter"] = "cubic-bezier(0, 0.4, 0, 1)",
        ["ease-exit"] = "cubic-bezier(0.6, 0, 0.8, 0.6)",
        ["ease-move"] = "cubic-bezier(0.4, 0, 0, 1)",
        // 進場的落定曲線：臨界阻尼的彈簧近似（阻尼比 1、無回彈），起步快、尾端長而柔，用在頁面與卡片的進場。
        ["ease-settle"] = "cubic-bezier(0.16, 1, 0.3, 1)",
        // 進場的位移量：只有一小段，方向與離場相反（從哪來、回哪去）。
        ["travel"] = "0.75rem",
        // 彈出層（選單）的位移與起始縮放：比頁面元素小一階，看起來是從觸發點長出來，不是滑進來。
        ["travel-sm"] = "0.25rem",
        ["scale"] = "0.97",
    };

    /// <summary>按下當下的縮放（回饋在按下，不是放開）。使用者要求減少動態時為 1。</summary>
    public const string PressScale = "0.97";

    /// <summary>可點的東西在觸控裝置上至少 44×44。</summary>
    public const string TouchTarget = "2.75rem";

    /// <summary>
    /// 三種版面：手機（&lt; <see cref="Tablet"/>）、平板（<see cref="Tablet"/> 起）、桌機（<see cref="Desktop"/> 起），
    /// 對應 768px 與 1280px 的常見裝置寬度。用 rem 不用 px：使用者把字級調大時，版面比螢幕寬度更早切成窄版。
    /// CSS 的媒體查詢不能讀自訂屬性，所以這裡是唯一的來源、由測試守住：樣式表的視窗寬度查詢只准用這兩個值。
    /// 元件自己的容器查詢（例如表格卡片）依容器寬度而不是螢幕寬度，不受此限。
    /// </summary>
    public static class Breakpoints
    {
        public const string Tablet = "48rem";

        public const string Desktop = "80rem";
    }

    public const string AppBarHeight = "64px";
}