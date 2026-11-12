using System.Globalization;
using System.Text;

namespace EnvCite.Platform.Web.Design;

/// <summary>
/// 把 <see cref="DesignTokens"/> 輸出成 CSS 自訂屬性。token 只有 C# 這一個來源；
/// 靜態的樣式規則（<c>wwwroot/design/platform.css</c>）只引用這些變數，不再寫死任何值。
/// </summary>
public static class TokenCss
{
    public static string Render()
    {
        var css = new StringBuilder();

        // 淺色是預設。預設跟隨系統：<html> 沒有 data-theme 時，深色只在瀏覽器／系統是深色時（prefers-color-scheme）套上同一組變數。
        // 使用者在個人設定手動選了才有 data-theme（伺服器依 cookie 輸出，見 App.razor）：選「暗色」無論系統如何都套深色，
        // 選「亮色」則擋住系統的深色。深色那一份變數只此一份，兩處共用同一個字串。
        // 選擇器用 html:root，特異度高於 MudBlazor 在 :root 上的規則，不論先後都贏。
        // color-scheme 預設 light dark，讓瀏覽器替表單控制項、捲軸、選取色跟著系統；手動選了就鎖定到同一邊，原生控制項才與 token 一致。
        css.Append(":root{").Append(Shared()).Append(Colors(DesignTokens.Light)).Append(Elevation(DesignTokens.Elevation)).Append("color-scheme:light dark;}");
        var dark = Colors(DesignTokens.Dark) + Elevation(DesignTokens.ElevationDark) + MudColors(DesignTokens.Dark);
        var (chosenLight, chosenDark) = (Chosen(Appearance.Light), Chosen(Appearance.Dark));
        css.Append(CultureInfo.InvariantCulture, $"@media (prefers-color-scheme:dark){{html:root:not({chosenLight}){{{dark}}}}}");
        css.Append(CultureInfo.InvariantCulture, $"html:root{chosenDark}{{{dark}color-scheme:dark;}}");
        css.Append(CultureInfo.InvariantCulture, $"html:root{chosenLight}{{color-scheme:light;}}");

        // 使用者偏好集中在 token 這一層，不在每個元件各寫一份：要較高對比，髮絲線與次要文字加深到控制項與正文的程度
        // （兩套主題各用自己的那一對）；要減少動態，位移與縮放歸零，元件只剩淡入淡出（第三方元件的動畫在樣式表收掉）。
        var light = DesignTokens.Light;
        var night = DesignTokens.Dark;
        var contrastDark = $"--ep-border-subtle:{night.BorderControl};--ep-text-secondary:{night.Text};";
        css.Append(CultureInfo.InvariantCulture, $"@media (prefers-contrast:more){{:root{{--ep-border-subtle:{light.BorderControl};--ep-text-secondary:{light.Text};}}}}");
        css.Append(CultureInfo.InvariantCulture, $"@media (prefers-contrast:more) and (prefers-color-scheme:dark){{html:root:not({chosenLight}){{{contrastDark}}}}}");
        css.Append(CultureInfo.InvariantCulture, $"@media (prefers-contrast:more){{html:root{chosenDark}{{{contrastDark}}}}}");
        css.Append("@media (prefers-reduced-motion:reduce){:root{--ep-press-scale:1;--ep-motion-scale:1;--ep-motion-travel:0rem;--ep-motion-travel-sm:0rem;}}");

        return css.ToString();
    }

    /// <summary>使用者手動選了這個外觀時 <c>&lt;html&gt;</c> 上的屬性選擇器；詞彙與 cookie、表單相同（<see cref="AppearanceNames.Name"/>）。</summary>
    private static string Chosen(Appearance appearance) => $"[data-theme=\"{appearance.Name()}\"]";

    private static string Colors(ColorTokens c) =>
        $"--ep-bg:{c.Background};--ep-surface:{c.Surface};--ep-surface-raised:{c.SurfaceRaised};--ep-surface-muted:{c.SurfaceMuted};" +
        $"--ep-text:{c.Text};--ep-text-secondary:{c.TextSecondary};" +
        $"--ep-primary:{c.Primary};--ep-on-primary:{c.OnPrimary};--ep-link:{c.Link};" +
        $"--ep-ink:{c.Ink};--ep-on-ink:{c.OnInk};--ep-accent:{c.Accent};" +
        $"--ep-success:{c.Success};--ep-warning:{c.Warning};--ep-error:{c.Error};--ep-info:{c.Info};" +
        $"--ep-border-subtle:{c.BorderSubtle};--ep-border-control:{c.BorderControl};--ep-focus:{c.Focus};--ep-scrim:{c.Scrim};";

    private static string Elevation(IReadOnlyDictionary<string, string> levels)
    {
        var css = new StringBuilder();
        foreach (var (key, value) in levels)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-elevation-{key}:{value};");
        }

        return css.ToString();
    }

    /// <summary>
    /// MudBlazor 自己用 <c>--mud-palette-*</c> 畫它的元件（表格、輸入框、對話框、選單、開關）。它的主題提供者只會輸出淺色那組，
    /// 深色時在這裡覆蓋成同一份深色調色盤；<c>html:root</c> 的特異度比它在 <c>:root</c> 上的規則高，所以不論先後都贏。顏色全部來自
    /// <see cref="ColorTokens"/>，衍生的明暗階與透明度在這裡算，不另寫色碼。
    /// </summary>
    private static string MudColors(ColorTokens c)
    {
        var css = new StringBuilder();

        void Role(string name, string color)
        {
            css.Append(CultureInfo.InvariantCulture, $"--mud-palette-{name}:{color};--mud-palette-{name}-rgb:{Rgb(color)};--mud-palette-{name}-text:{c.OnPrimary};");
            css.Append(CultureInfo.InvariantCulture, $"--mud-palette-{name}-darken:{Mix(color, "#000000", 0.15)};--mud-palette-{name}-lighten:{Mix(color, "#FFFFFF", 0.15)};");
            css.Append(CultureInfo.InvariantCulture, $"--mud-palette-{name}-hover:rgba({Rgb(color)},0.12);");
        }

        Role("primary", c.Primary);
        Role("success", c.Success);
        Role("info", c.Info);
        Role("warning", c.Warning);
        Role("error", c.Error);

        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-background:{c.Background};--mud-palette-background-gray:{c.SurfaceMuted};--mud-palette-surface:{c.SurfaceRaised};");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-drawer-background:{c.Surface};--mud-palette-drawer-text:{c.Text};--mud-palette-drawer-icon:{c.TextSecondary};");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-appbar-background:{c.Surface};--mud-palette-appbar-text:{c.Text};");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-text-primary:{c.Text};--mud-palette-text-secondary:{c.TextSecondary};--mud-palette-text-disabled:rgba({Rgb(c.Text)},0.38);");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-action-default:{c.TextSecondary};--mud-palette-action-default-hover:rgba({Rgb(c.Text)},0.08);");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-action-disabled:rgba({Rgb(c.Text)},0.3);--mud-palette-action-disabled-background:rgba({Rgb(c.Text)},0.12);");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-divider:{c.BorderSubtle};--mud-palette-divider-light:{c.BorderSubtle};--mud-palette-lines-default:{c.BorderSubtle};--mud-palette-lines-inputs:{c.BorderControl};");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-table-lines:{c.BorderSubtle};--mud-palette-table-striped:rgba({Rgb(c.Text)},0.03);--mud-palette-table-hover:rgba({Rgb(c.Text)},0.05);");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-gray-default:{c.TextSecondary};--mud-palette-gray-light:{c.BorderControl};--mud-palette-gray-lighter:{c.SurfaceMuted};--mud-palette-gray-dark:{c.TextSecondary};--mud-palette-gray-darker:{c.Text};");
        css.Append(CultureInfo.InvariantCulture, $"--mud-palette-overlay-dark:{c.Scrim};--mud-palette-overlay-light:rgba({Rgb(c.Background)},0.5);");
        return css.ToString();
    }

    private static string Rgb(string hex) =>
        string.Join(',', Enumerable.Range(0, 3).Select(i => int.Parse(hex.AsSpan(1 + (i * 2), 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture)));

    /// <summary>把 <paramref name="color"/> 往 <paramref name="toward"/> 混 <paramref name="amount"/>（0～1），回傳 #RRGGBB。</summary>
    private static string Mix(string color, string toward, double amount)
    {
        static int Channel(string hex, int index) => int.Parse(hex.AsSpan(1 + (index * 2), 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture);

        return "#" + string.Concat(Enumerable.Range(0, 3).Select(i =>
            ((int)Math.Round((Channel(color, i) * (1 - amount)) + (Channel(toward, i) * amount))).ToString("X2", CultureInfo.InvariantCulture)));
    }

    private static string Shared()
    {
        var css = new StringBuilder();
        css.Append("--ep-font:").Append(Join(DesignTokens.FontFamily)).Append(';');
        css.Append("--ep-code-features:").Append(DesignTokens.CodeFeatures).Append(';');
        foreach (var (name, style) in DesignTokens.Type)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-{name}-size:{style.Size};--ep-{name}-leading:{style.LineHeight};")
                .Append(CultureInfo.InvariantCulture, $"--ep-{name}-tracking:{style.LetterSpacing};--ep-{name}-weight:{style.Weight};");
        }

        foreach (var (key, value) in DesignTokens.Space)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-space-{key}:{value};");
        }

        foreach (var (key, value) in DesignTokens.Measure)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-measure-{key}:{value};");
        }

        foreach (var (key, value) in DesignTokens.Radius)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-radius-{key}:{value};");
        }

        foreach (var (key, value) in DesignTokens.Motion)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-motion-{key}:{value};");
        }

        css.Append(CultureInfo.InvariantCulture, $"--ep-press-scale:{DesignTokens.PressScale};--ep-touch-target:{DesignTokens.TouchTarget};");
        css.Append(CultureInfo.InvariantCulture, $"--ep-appbar-height:{DesignTokens.AppBarHeight};");
        css.Append(CultureInfo.InvariantCulture, $"--ep-qr-surface:{DesignTokens.QrSurface};");
        return css.ToString();
    }

    // 含空白的字型名稱要加引號。
    private static string Join(IEnumerable<string> fonts) =>
        string.Join(',', fonts.Select(f => f.Contains(' ', StringComparison.Ordinal) ? $"\"{f}\"" : f));
}