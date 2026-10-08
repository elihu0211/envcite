namespace EnvLaw.Platform.Web.Design;

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
    // 品牌主色與 chepb 同源：深綠 #006030，中性灰階用 slate。狀態色全在 sRGB 色域內、彩度有上限
    // （狀態要比品牌色顯眼，但不能搶過它），而且是文字級：對白底與一成淡底（徽章、提示框的字）都 ≥ 4.5:1。
    // 警示與危險兩色在這個明度本來就近，所以永遠搭配圖示與文字，不靠色相分辨。
    public static ColorTokens Light { get; } = new()
    {
        Background = "#F8FAFC",
        Surface = "#FFFFFF",
        SurfaceRaised = "#FFFFFF",
        SurfaceMuted = "#F1F5F9",
        Text = "#0F172A",
        TextSecondary = "#475569",
        Primary = "#006030",
        OnPrimary = "#FFFFFF",
        Link = "#006030",
        Success = "#0B7857",
        Warning = "#AC5107",
        Error = "#AC312C",
        Info = "#1D52AF",
        BorderSubtle = "#E2E8F0",
        BorderControl = "#7D8CA3",
        Focus = "#006030",
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

    public const string DrawerWidth = "260px";

    /// <summary>平板的左側窄欄寬度（圖示在上、文字在下）。</summary>
    public const string NavRailWidth = "5.5rem";

    /// <summary>手機底部分頁列、平板窄欄每一項的最小高度（至少 44px 觸控目標）。</summary>
    public const string NavItemHeight = "3.5rem";

    public const string AppBarHeight = "64px";
}