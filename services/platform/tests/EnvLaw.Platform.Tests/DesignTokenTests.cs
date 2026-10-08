using System.Globalization;
using System.Net;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

using EnvLaw.Platform.Web.Design;

namespace EnvLaw.Platform.Tests;

/// <summary>設計 token 的規則：對比度（WCAG AA）、單一來源、輸出完整。純函式，不需要資料庫。</summary>
public static partial class DesignTokenTests
{
    private static double Luminance(string hex)
    {
        static double Channel(int value)
        {
            var c = value / 255.0;
            return c <= 0.03928 ? c / 12.92 : Math.Pow((c + 0.055) / 1.055, 2.4);
        }

        var h = hex.TrimStart('#');
        return (0.2126 * Channel(int.Parse(h[..2], NumberStyles.HexNumber, CultureInfo.InvariantCulture)))
            + (0.7152 * Channel(int.Parse(h[2..4], NumberStyles.HexNumber, CultureInfo.InvariantCulture)))
            + (0.0722 * Channel(int.Parse(h[4..6], NumberStyles.HexNumber, CultureInfo.InvariantCulture)));
    }

    private static double Contrast(string a, string b)
    {
        var (hi, lo) = (Math.Max(Luminance(a), Luminance(b)), Math.Min(Luminance(a), Luminance(b)));
        return (hi + 0.05) / (lo + 0.05);
    }

    public static TheoryData<string, string, string, double> TextPairs()
    {
        var data = new TheoryData<string, string, string, double>();
        foreach (var (name, c) in new[] { ("light", DesignTokens.Light), ("dark", DesignTokens.Dark) })
        {
            // 一般文字 4.5:1。
            data.Add(name, "text on background", $"{c.Text}|{c.Background}", 4.5);
            data.Add(name, "text on surface", $"{c.Text}|{c.Surface}", 4.5);
            data.Add(name, "secondary text on background", $"{c.TextSecondary}|{c.Background}", 4.5);
            data.Add(name, "secondary text on surface", $"{c.TextSecondary}|{c.Surface}", 4.5);
            data.Add(name, "on-primary on primary", $"{c.OnPrimary}|{c.Primary}", 4.5);
            data.Add(name, "link on surface", $"{c.Link}|{c.Surface}", 4.5);
            data.Add(name, "link on background", $"{c.Link}|{c.Background}", 4.5);
            data.Add(name, "success on surface", $"{c.Success}|{c.Surface}", 4.5);
            data.Add(name, "warning on surface", $"{c.Warning}|{c.Surface}", 4.5);
            data.Add(name, "error on surface", $"{c.Error}|{c.Surface}", 4.5);
            data.Add(name, "info on surface", $"{c.Info}|{c.Surface}", 4.5);
            // 控制項外框與焦點環是非文字元件，3:1。
            data.Add(name, "control border on surface", $"{c.BorderControl}|{c.Surface}", 3.0);
            data.Add(name, "focus ring on surface", $"{c.Focus}|{c.Surface}", 3.0);
            data.Add(name, "primary button against surface", $"{c.Primary}|{c.Surface}", 3.0);
        }

        return data;
    }

    [Theory]
    [MemberData(nameof(TextPairs))]
    public static void ColorPairsMeetWcagAa(string scheme, string pair, string colors, double minimum)
    {
        var parts = colors.Split('|');

        var ratio = Contrast(parts[0], parts[1]);

        Assert.True(ratio >= minimum, $"{scheme}: {pair} is {ratio:F2}:1, needs {minimum}:1");
    }

    [Fact]
    public static void TheCssDefinesEveryColorTokenForBothSchemes()
    {
        var css = TokenCss.Render();

        foreach (var variable in new[]
        {
            "--ep-bg", "--ep-surface", "--ep-text", "--ep-text-secondary", "--ep-primary", "--ep-on-primary", "--ep-link",
            "--ep-success", "--ep-warning", "--ep-error", "--ep-info", "--ep-border-subtle", "--ep-border-control",
            "--ep-focus",
        })
        {
            // 淺色（:root）與深色（媒體查詢 + data-theme）各一次。
            Assert.Equal(3, Regex.Count(css, Regex.Escape(variable + ":")));
        }

        Assert.Contains("prefers-color-scheme: dark", css, StringComparison.Ordinal);
        Assert.Contains("--ep-font:system-ui", css, StringComparison.Ordinal);
        Assert.Contains("\"PingFang TC\"", css, StringComparison.Ordinal);
    }

    [Fact]
    public static void LargeTextTightensTrackingAndSmallTextLoosensIt()
    {
        static double Em(DesignTokens.TypeStyle s) =>
            double.Parse(s.LetterSpacing.Replace("em", string.Empty, StringComparison.Ordinal), CultureInfo.InvariantCulture);

        Assert.True(Em(DesignTokens.Display) < Em(DesignTokens.H1));
        Assert.True(Em(DesignTokens.H1) < Em(DesignTokens.H3));
        Assert.True(Em(DesignTokens.Body) <= Em(DesignTokens.BodySmall));
        Assert.True(Em(DesignTokens.BodySmall) < Em(DesignTokens.Caption));
    }

    [Fact]
    public static void TheThemeIsBuiltFromTheTokens()
    {
        var theme = PlatformTheme.Create();

        // MudBlazor 的色碼輸出可能小寫、可能帶透明度；只比 RGB。
        Assert.Equal(
            DesignTokens.Light.Primary,
            theme.PaletteLight.Primary.ToString(MudBlazor.Utilities.MudColorOutputFormats.Hex)[..7],
            ignoreCase: true);
        Assert.Equal(
            DesignTokens.Dark.Primary,
            theme.PaletteDark.Primary.ToString(MudBlazor.Utilities.MudColorOutputFormats.Hex)[..7],
            ignoreCase: true);
        Assert.Equal("none", theme.Typography.Button.TextTransform);
    }

    // token 只有 DesignTokens.cs 一個來源：靜態樣式表不得自己寫色碼。
    [Fact]
    public static void TheStylesheetHardCodesNoColors()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.DoesNotMatch(HexColor(), css);
        Assert.DoesNotMatch(RgbColor(), css);
        // 不用漸層：平的表面、中性灰階加一個強調色。
        Assert.DoesNotContain("gradient", css, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("gradient", TokenCss.Render(), StringComparison.OrdinalIgnoreCase);
        Assert.Contains("prefers-reduced-motion", css, StringComparison.Ordinal);
        Assert.Contains("prefers-reduced-transparency", css, StringComparison.Ordinal);
        Assert.Contains("prefers-contrast", css, StringComparison.Ordinal);
    }

    // 響應式用原生 CSS：容器查詢、觸控目標、hover 只給能 hover 的裝置、安全區；斷點用 rem，不用 px。
    [Fact]
    public static void TheStylesheetIsResponsiveWithNativeCss()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Contains("container: ep-table / inline-size", css, StringComparison.Ordinal);
        Assert.Contains("@container ep-table", css, StringComparison.Ordinal);
        Assert.Contains("@media (pointer: coarse)", css, StringComparison.Ordinal);
        Assert.Contains("@media (hover: hover)", css, StringComparison.Ordinal);
        Assert.Contains("env(safe-area-inset-left)", css, StringComparison.Ordinal);
        Assert.Contains("2.75rem", css, StringComparison.Ordinal); // 觸控目標至少 44px
        Assert.DoesNotMatch(PixelBreakpoint(), css);
        // 表格卡片不能是 overflow: hidden：會把寬表格的右半邊裁掉，看不到也捲不到。
        Assert.DoesNotMatch(HiddenOverflowOnCard(), css);
    }

    [Fact]
    public static void TheLargestTypeSizesAreFluid()
    {
        Assert.Contains("clamp(", DesignTokens.Display.Size, StringComparison.Ordinal);
        Assert.Contains("clamp(", DesignTokens.H1.Size, StringComparison.Ordinal);
        Assert.Contains("clamp(", TokenCss.Render(), StringComparison.Ordinal);
    }

    private static string StylesheetPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvLaw.Platform.Web", "wwwroot", "design", "platform.css"));

    [GeneratedRegex(@"#[0-9a-fA-F]{3,8}\b")]
    private static partial Regex HexColor();

    [GeneratedRegex(@"\brgba?\(")]
    private static partial Regex RgbColor();

    [GeneratedRegex(@"@(media|container)[^{]*\d+px")]
    private static partial Regex PixelBreakpoint();

    [GeneratedRegex(@"\.ep-table-card\s*\{[^}]*overflow:\s*hidden")]
    private static partial Regex HiddenOverflowOnCard();
}

[Collection(PlatformTestGroup.Name)]
public sealed class DesignPageTests(PlatformFactory factory)
{
    [Fact]
    public async Task AnAnonymousPageCarriesTheTokensAndNoThirdPartyRequests()
    {
        using var client = factory.CreateClient(new Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var response = await client.GetAsync(new Uri("/ui/denied", UriKind.Relative));
        var html = await response.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("--ep-primary:", html, StringComparison.Ordinal);
        Assert.Contains("design/platform", html, StringComparison.Ordinal);
        Assert.Contains("lang=\"zh-TW\"", html, StringComparison.Ordinal);
        // 手機：延伸到瀏海下（再用安全區內縮）、不禁用縮放、瀏覽器工具列顏色跟著主題（顏色來自 token）。
        Assert.Contains("viewport-fit=cover", html, StringComparison.Ordinal);
        Assert.DoesNotContain("user-scalable", html, StringComparison.Ordinal);
        Assert.DoesNotContain("maximum-scale", html, StringComparison.Ordinal);
        Assert.Contains($"<meta name=\"theme-color\" content=\"{DesignTokens.Light.Background}\" media=\"(prefers-color-scheme: light)\"", html, StringComparison.Ordinal);
        Assert.Contains($"<meta name=\"theme-color\" content=\"{DesignTokens.Dark.Background}\" media=\"(prefers-color-scheme: dark)\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("fonts.googleapis.com", html, StringComparison.Ordinal);
        Assert.Contains("<h1", html, StringComparison.Ordinal);
    }

    // 頁面只有 HTML 沒有互動連線也看起來正常，所以只測 HTML 抓不到「框架腳本 404」：
    // 主機專案自己沒有 .razor 檔時，SDK 不會附上 blazor.web.js（見 Api.csproj 的 RequiresAspNetWebAssets）。
    // 這裡真的去抓頁面引用的腳本。
    [Fact]
    public async Task TheScriptsThePageReferencesAreServed()
    {
        using var client = factory.CreateClient(new Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        var html = await client.GetStringAsync(new Uri("/ui/denied", UriKind.Relative));

        var sources = Regex.Matches(html, "<script src=\"([^\"]+)\"")
            .Select(m => m.Groups[1].Value)
            .ToList();

        Assert.Contains(sources, s => s.Contains("blazor.web", StringComparison.Ordinal));
        foreach (var source in sources)
        {
            using var response = await client.GetAsync(new Uri(source, UriKind.RelativeOrAbsolute));
            Assert.True(response.StatusCode == HttpStatusCode.OK, $"{source} -> {(int)response.StatusCode}");
        }
    }
}