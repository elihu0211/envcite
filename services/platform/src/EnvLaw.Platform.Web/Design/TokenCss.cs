using System.Globalization;
using System.Text;

namespace EnvLaw.Platform.Web.Design;

/// <summary>
/// 把 <see cref="DesignTokens"/> 輸出成 CSS 自訂屬性。token 只有 C# 這一個來源；
/// 靜態的樣式規則（<c>wwwroot/design/platform.css</c>）只引用這些變數，不再寫死任何值。
/// </summary>
public static class TokenCss
{
    public static string Render()
    {
        var css = new StringBuilder();

        // 只有淺色：產品決定不做深色模式。color-scheme 宣告 light，瀏覽器就不會替表單控制項、捲軸套深色，
        // 系統設成深色時頁面也維持淺色。
        css.Append(":root{").Append(Shared()).Append(Colors(DesignTokens.Light)).Append("color-scheme:light;}");

        return css.ToString();
    }

    private static string Colors(ColorTokens c) =>
        $"--ep-bg:{c.Background};--ep-surface:{c.Surface};--ep-surface-raised:{c.SurfaceRaised};" +
        $"--ep-text:{c.Text};--ep-text-secondary:{c.TextSecondary};" +
        $"--ep-primary:{c.Primary};--ep-on-primary:{c.OnPrimary};--ep-link:{c.Link};" +
        $"--ep-success:{c.Success};--ep-warning:{c.Warning};--ep-error:{c.Error};--ep-info:{c.Info};" +
        $"--ep-border-subtle:{c.BorderSubtle};--ep-border-control:{c.BorderControl};--ep-focus:{c.Focus};";

    private static string Shared()
    {
        var css = new StringBuilder();
        css.Append("--ep-font:").Append(Join(DesignTokens.FontFamily)).Append(';');
        css.Append("--ep-font-mono:").Append(Join(DesignTokens.FontFamilyMono)).Append(';');
        foreach (var (name, style) in new (string, DesignTokens.TypeStyle)[]
        {
            ("display", DesignTokens.Display), ("h1", DesignTokens.H1), ("h2", DesignTokens.H2), ("h3", DesignTokens.H3),
            ("body", DesignTokens.Body), ("body-sm", DesignTokens.BodySmall), ("caption", DesignTokens.Caption),
            ("button", DesignTokens.Button),
        })
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-{name}-size:{style.Size};--ep-{name}-leading:{style.LineHeight};")
                .Append(CultureInfo.InvariantCulture, $"--ep-{name}-tracking:{style.LetterSpacing};--ep-{name}-weight:{style.Weight};");
        }

        foreach (var (key, value) in DesignTokens.Space)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-space-{key}:{value};");
        }

        foreach (var (key, value) in DesignTokens.Radius)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-radius-{key}:{value};");
        }

        foreach (var (key, value) in DesignTokens.Elevation)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-elevation-{key}:{value};");
        }

        foreach (var (key, value) in DesignTokens.Motion)
        {
            css.Append(CultureInfo.InvariantCulture, $"--ep-motion-{key}:{value};");
        }

        css.Append(CultureInfo.InvariantCulture, $"--ep-drawer-width:{DesignTokens.DrawerWidth};--ep-appbar-height:{DesignTokens.AppBarHeight};");
        return css.ToString();
    }

    // 含空白的字型名稱要加引號。
    private static string Join(IEnumerable<string> fonts) =>
        string.Join(',', fonts.Select(f => f.Contains(' ', StringComparison.Ordinal) ? $"\"{f}\"" : f));
}