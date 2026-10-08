namespace EnvLaw.Platform.Web.Design;

/// <summary>
/// 一組配色。語意名稱而非色名：元件只說「這是主要文字」「這是危險」，換主題只換這張表。
/// 文字配對的對比度由 <c>DesignTokenTests</c> 驗證（WCAG AA：一般文字 4.5:1、控制項外框 3:1）。
/// </summary>
public sealed record ColorTokens
{
    public required string Background { get; init; }

    public required string Surface { get; init; }

    public required string SurfaceRaised { get; init; }

    public required string Text { get; init; }

    public required string TextSecondary { get; init; }

    public required string Primary { get; init; }

    /// <summary>放在 <see cref="Primary"/> 上的文字。</summary>
    public required string OnPrimary { get; init; }

    /// <summary>文字連結的顏色。與 <see cref="Primary"/>（按鈕等填色）分開：深色背景上，填色的藍當文字太暗，要用較亮的藍。</summary>
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
}

public static class DesignTokens
{
    // 配色取自蘋果的網站與系統色：中性灰階 + 單一藍色作為強調色，不用漸層、不用深藍底。
    // 蘋果自己的 systemBlue、systemGreen 當文字並不都過 WCAG AA，所以文字用的語意色取蘋果「提高對比」的變體。
    public static ColorTokens Light { get; } = new()
    {
        Background = "#F5F5F7",
        Surface = "#FFFFFF",
        SurfaceRaised = "#FFFFFF",
        Text = "#1D1D1F",
        TextSecondary = "#6E6E73",
        Primary = "#0071E3",
        OnPrimary = "#FFFFFF",
        Link = "#0066CC",
        Success = "#217A37",
        Warning = "#B25000",
        Error = "#D70015",
        Info = "#0066CC",
        BorderSubtle = "#D2D2D7",
        BorderControl = "#86868B",
        Focus = "#0071E3",
    };

    public static ColorTokens Dark { get; } = new()
    {
        Background = "#000000",
        Surface = "#1D1D1F",
        SurfaceRaised = "#2C2C2E",
        Text = "#F5F5F7",
        TextSecondary = "#A1A1A6",
        Primary = "#0071E3",
        OnPrimary = "#FFFFFF",
        Link = "#2997FF",
        Success = "#30D158",
        Warning = "#FF9F0A",
        Error = "#FF453A",
        Info = "#64D2FF",
        BorderSubtle = "#424245",
        BorderControl = "#86868B",
        Focus = "#2997FF",
    };

    // ---- 字型 ----------------------------------------------------------------------------------
    // 預設用平台系統字型：已內建光學尺寸與字距調校，也不對第三方發請求。繁中字型依平台列出，
    // 最後退到 sans-serif。

    public static string[] FontFamily { get; } =
    [
        "system-ui", "-apple-system", "Segoe UI", "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", "sans-serif",
    ];

    public static string[] FontFamilyMono { get; } =
        ["ui-monospace", "SF Mono", "Menlo", "Consolas", "monospace"];

    /// <summary>
    /// 字級階層：字距與行高隨字級變化（大字收緊、小字略放寬），不是一個固定值。
    /// 中文內文行高取 1.6 以上。單位 rem，使用者調整瀏覽器字級時版面跟著放大。
    /// </summary>
    public sealed record TypeStyle(string Size, string LineHeight, string LetterSpacing, int Weight);

    public static TypeStyle Display { get; } = new("clamp(2rem, 1.4rem + 2vw, 2.5rem)", "1.1", "-0.02em", 700);

    public static TypeStyle H1 { get; } = new("clamp(1.625rem, 1.3rem + 1vw, 2rem)", "1.2", "-0.015em", 700);

    public static TypeStyle H2 { get; } = new("1.5rem", "1.25", "-0.01em", 600);

    public static TypeStyle H3 { get; } = new("1.25rem", "1.3", "-0.005em", 600);

    public static TypeStyle Body { get; } = new("1rem", "1.6", "0", 400);

    public static TypeStyle BodySmall { get; } = new("0.875rem", "1.55", "0.005em", 400);

    public static TypeStyle Caption { get; } = new("0.75rem", "1.4", "0.01em", 400);

    public static TypeStyle Button { get; } = new("0.875rem", "1.25", "0.005em", 600);

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
        ["control"] = "6px",
        ["card"] = "10px",
        ["sheet"] = "16px",
    };

    /// <summary>越大的面越厚：陰影與模糊都隨之加重。</summary>
    public static IReadOnlyDictionary<string, string> Elevation { get; } = new Dictionary<string, string>
    {
        ["1"] = "0 1px 2px rgb(16 24 40 / 0.06), 0 1px 3px rgb(16 24 40 / 0.1)",
        ["2"] = "0 4px 8px -2px rgb(16 24 40 / 0.1), 0 2px 4px -2px rgb(16 24 40 / 0.06)",
        ["3"] = "0 12px 16px -4px rgb(16 24 40 / 0.12), 0 4px 6px -2px rgb(16 24 40 / 0.05)",
    };

    public static IReadOnlyDictionary<string, string> Motion { get; } = new Dictionary<string, string>
    {
        ["fast"] = "120ms",
        ["base"] = "200ms",
        ["ease"] = "cubic-bezier(0.2, 0, 0, 1)",
    };

    public const string DrawerWidth = "260px";

    public const string AppBarHeight = "64px";
}