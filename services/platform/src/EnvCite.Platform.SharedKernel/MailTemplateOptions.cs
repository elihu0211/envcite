using System.Globalization;
using System.Text.RegularExpressions;

using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// <c>Platform:Mail:Template</c> 區段：信件版型的可調值（語言、寬度、內距、字型、色票），全部有預設值，部署者可逐鍵覆寫。
/// 文案不在這裡（在 <see cref="MailTexts"/> 資源檔，隨語言）；這裡只有「長相」。值會被放進內嵌樣式與樣式表，所以格式與對比度在啟動時驗證
/// （<see cref="MailTemplateOptionsValidator"/>），不合法就停下並指名鍵——設定寫錯不會變成一封看不清或被注入的信。
/// </summary>
public sealed partial class MailTemplateOptions : ISectionOptions
{
    /// <summary>文字對比度的下限（WCAG AA，一般文字）。</summary>
    public const double MinimumContrast = 4.5;

    public const string TraditionalChinese = "zh-TW";

    public const string English = "en";

    public static string SectionName => "Platform:Mail:Template";

    /// <summary>信件的語言（<see cref="TraditionalChinese"/> 或 <see cref="English"/>）。信是寄給還沒登入過的人，沒有「他的語言」可看，所以由部署決定，不隨操作者的介面語言。</summary>
    public string Culture { get; set; } = TraditionalChinese;

    /// <summary>信件內容的最大寬度（px）。</summary>
    public int Width { get; set; } = 600;

    public int PaddingVertical { get; set; } = 28;

    public int PaddingHorizontal { get; set; } = 32;

    /// <summary>視窗寬度在這個值（px）以下時改用 <see cref="CompactPaddingVertical"/> 與 <see cref="CompactPaddingHorizontal"/>。</summary>
    public int CompactBreakpoint { get; set; } = 480;

    public int CompactPaddingVertical { get; set; } = 22;

    public int CompactPaddingHorizontal { get; set; } = 18;

    public string FontFamily { get; set; } = "'PingFang TC','Microsoft JhengHei','Noto Sans TC',system-ui,sans-serif";

    public string MonospaceFontFamily { get; set; } = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Courier New',monospace";

    public MailPalette Light { get; set; } = new()
    {
        Page = "#eef0f2",
        Card = "#ffffff",
        Text = "#1f2328",
        Muted = "#59636e",
        Line = "#d8dde2",
        Soft = "#f3f5f7",
        Brand = "#185fa5",
        Button = "#185fa5",
        ButtonText = "#ffffff",
    };

    public MailPalette Dark { get; set; } = new()
    {
        Page = "#16191d",
        Card = "#1f242a",
        Text = "#e6e9ec",
        Muted = "#a3adb8",
        Line = "#343b43",
        Soft = "#2a3038",
        Brand = "#85b7eb",
        Button = "#85b7eb",
        ButtonText = "#042c53",
    };

    /// <summary>Outlook 的 VML 按鈕要固定寬度：內容寬度扣掉左右內距。</summary>
    internal int ButtonWidth => Width - (2 * PaddingHorizontal);

    [GeneratedRegex(@"^#[0-9a-fA-F]{6}$", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    internal static partial Regex HexColor();

    [GeneratedRegex(@"^[\p{L}\p{N} ,'\-_]{1,200}$", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    internal static partial Regex FontStack();
}

/// <summary>一組配色（淺色或深色），每個值是 <c>#rrggbb</c>。</summary>
public sealed class MailPalette
{
    /// <summary>信件外的頁面底色。</summary>
    public string Page { get; set; } = string.Empty;

    /// <summary>內容卡片的底色。</summary>
    public string Card { get; set; } = string.Empty;

    public string Text { get; set; } = string.Empty;

    /// <summary>次要文字（說明、頁尾）。</summary>
    public string Muted { get; set; } = string.Empty;

    public string Line { get; set; } = string.Empty;

    /// <summary>租戶與備用連結區塊的淺底。</summary>
    public string Soft { get; set; } = string.Empty;

    public string Brand { get; set; } = string.Empty;

    public string Button { get; set; } = string.Empty;

    public string ButtonText { get; set; } = string.Empty;

    internal IEnumerable<(string Key, string Value)> Colors =>
    [
        (nameof(Page), Page), (nameof(Card), Card), (nameof(Text), Text), (nameof(Muted), Muted), (nameof(Line), Line),
        (nameof(Soft), Soft), (nameof(Brand), Brand), (nameof(Button), Button), (nameof(ButtonText), ButtonText),
    ];
}

/// <summary>
/// 驗證版型選項：值會進內嵌樣式與樣式表，所以顏色只收 <c>#rrggbb</c>、字型只收字母數字與少數符號；
/// 跨欄位規則是每組色票的文字對比度至少 <see cref="MailTemplateOptions.MinimumContrast"/>（所以調色票不能把信調成看不清）。
/// 預設值已通過；與 <c>Platform:Mail:Enabled</c> 無關，一律驗證（預覽命令也用同一份）。
/// </summary>
internal sealed class MailTemplateOptionsValidator : IValidateOptions<MailTemplateOptions>
{
    private const string Section = "Platform:Mail:Template";

    public ValidateOptionsResult Validate(string? name, MailTemplateOptions options)
    {
        var failures = new List<string>();
        if (options.Culture is not (MailTemplateOptions.TraditionalChinese or MailTemplateOptions.English))
        {
            failures.Add($"{Section}:Culture must be '{MailTemplateOptions.TraditionalChinese}' or '{MailTemplateOptions.English}'.");
        }

        Range(failures, nameof(options.Width), options.Width, 320, 800);
        Range(failures, nameof(options.PaddingVertical), options.PaddingVertical, 0, 64);
        Range(failures, nameof(options.PaddingHorizontal), options.PaddingHorizontal, 0, 64);
        Range(failures, nameof(options.CompactBreakpoint), options.CompactBreakpoint, 320, 767);
        Range(failures, nameof(options.CompactPaddingVertical), options.CompactPaddingVertical, 0, 64);
        Range(failures, nameof(options.CompactPaddingHorizontal), options.CompactPaddingHorizontal, 0, 64);
        if (options.ButtonWidth < 240)
        {
            failures.Add($"{Section}:Width minus twice {Section}:PaddingHorizontal must be at least 240.");
        }

        Font(failures, nameof(options.FontFamily), options.FontFamily);
        Font(failures, nameof(options.MonospaceFontFamily), options.MonospaceFontFamily);
        Palette(failures, nameof(options.Light), options.Light);
        Palette(failures, nameof(options.Dark), options.Dark);

        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }

    private static void Range(List<string> failures, string key, int value, int min, int max)
    {
        if (value < min || value > max)
        {
            failures.Add($"{Section}:{key} must be between {min} and {max}.");
        }
    }

    private static void Font(List<string> failures, string key, string value)
    {
        if (!MailTemplateOptions.FontStack().IsMatch(value ?? string.Empty))
        {
            failures.Add($"{Section}:{key} must be a font list of letters, digits, spaces, commas, hyphens, underscores and single quotes (at most 200 characters).");
        }
    }

    private static void Palette(List<string> failures, string key, MailPalette palette)
    {
        var valid = true;
        foreach (var (color, value) in palette.Colors)
        {
            if (!MailTemplateOptions.HexColor().IsMatch(value ?? string.Empty))
            {
                failures.Add($"{Section}:{key}:{color} must be a #rrggbb color.");
                valid = false;
            }
        }

        if (!valid)
        {
            return;
        }

        (string Text, string Foreground, string Background)[] pairs =
        [
            (nameof(palette.Text), palette.Text, palette.Card),
            (nameof(palette.Muted), palette.Muted, palette.Card),
            (nameof(palette.Muted), palette.Muted, palette.Page),
            (nameof(palette.Muted), palette.Muted, palette.Soft),
            (nameof(palette.Text), palette.Text, palette.Soft),
            (nameof(palette.Brand), palette.Brand, palette.Card),
            (nameof(palette.ButtonText), palette.ButtonText, palette.Button),
        ];
        foreach (var (color, foreground, background) in pairs)
        {
            var ratio = Contrast(foreground, background);
            if (ratio < MailTemplateOptions.MinimumContrast)
            {
                failures.Add(string.Create(
                    CultureInfo.InvariantCulture,
                    $"{Section}:{key}:{color} ({foreground}) on its background ({background}) has contrast {ratio:F2}:1; at least {MailTemplateOptions.MinimumContrast}:1 is required."));
            }
        }
    }

    /// <summary>WCAG 2.x 的對比度：(較亮的相對亮度 + 0.05) / (較暗的 + 0.05)。</summary>
    private static double Contrast(string foreground, string background)
    {
        var (a, b) = (Luminance(foreground), Luminance(background));
        return (Math.Max(a, b) + 0.05) / (Math.Min(a, b) + 0.05);
    }

    private static double Luminance(string hex) =>
        (0.2126 * Channel(hex[1..3])) + (0.7152 * Channel(hex[3..5])) + (0.0722 * Channel(hex[5..7]));

    private static double Channel(string pair)
    {
        var value = int.Parse(pair, NumberStyles.HexNumber, CultureInfo.InvariantCulture) / 255.0;
        return value <= 0.03928 ? value / 12.92 : Math.Pow((value + 0.055) / 1.055, 2.4);
    }
}