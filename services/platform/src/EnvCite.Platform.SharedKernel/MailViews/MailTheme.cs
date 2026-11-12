using System.Globalization;

namespace EnvCite.Platform.SharedKernel.MailViews;

/// <summary>
/// 版型的長相：由 <see cref="MailTemplateOptions"/>（已驗證）算出每個元素的內嵌樣式與樣式表。
/// 傳統 Outlook 不認樣式表的規則，所以淺色一律內嵌；深色與手機的內距只能靠樣式表，不支援的客戶端維持淺色與桌面內距。
/// 進樣式表的值只有整數與 <c>#rrggbb</c>（驗證過），字型只進內嵌樣式（由 Razor 編碼屬性值）。
/// </summary>
public sealed class MailTheme(MailTemplateOptions options)
{
    private static string Px(int value) => value.ToString(CultureInfo.InvariantCulture);

    public MailPalette Light => options.Light;

    public MailPalette Dark => options.Dark;

    public string Width => Px(options.Width);

    /// <summary>Outlook 按鈕（VML）的固定寬度。</summary>
    public string ButtonWidth => Px(options.ButtonWidth);

    public string Body => $"margin:0;padding:0;background-color:{Light.Page};";

    public const string Preheader = "display:none;visibility:hidden;opacity:0;color:transparent;height:0;width:0;max-height:0;max-width:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;";

    /// <summary>預覽文字後面補的不可見空白，避免客戶端在收件匣預覽裡接著顯示內文的開頭。</summary>
    public static readonly string PreheaderFiller = string.Concat(Enumerable.Repeat("&zwnj;&nbsp;", 60));

    public string Page => $"background-color:{Light.Page};";

    public const string Outer = "padding:24px 12px;";

    public string Table => $"width:100%;max-width:{Width}px;";

    /// <summary>Outlook 不認 <c>max-width</c>：條件註解包一張固定寬度的表格。內容是整數，沒有使用者的值。</summary>
    public string GhostOpen =>
        $"<!--[if mso]><table role=\"presentation\" align=\"center\" width=\"{Width}\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\"><tr><td width=\"{Width}\"><![endif]-->";

    public const string GhostClose = "<!--[if mso]></td></tr></table><![endif]-->";

    public const string OutlookHead =
        "<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>"
        + "<style>table, td, h1, p, a, center { font-family: 'Microsoft JhengHei', Arial, sans-serif !important; }</style><![endif]-->";

    public string Card =>
        $"padding:{Px(options.PaddingVertical)}px {Px(options.PaddingHorizontal)}px;background-color:{Light.Card};border-radius:12px;font-family:{options.FontFamily};color:{Light.Text};";

    public string BrandCell =>
        $"padding:0 0 14px 0;border-bottom:1px solid {Light.Line};font-size:14px;line-height:1.4;font-weight:500;letter-spacing:.08em;color:{Light.Brand};";

    public const string HeadingRow = "padding:22px 0 0 0;";

    public string Heading => $"margin:0;font-size:21px;line-height:1.4;font-weight:500;color:{Light.Text};";

    public string LeadRow => $"padding:10px 0 0 0;font-size:15px;line-height:1.7;color:{Light.Text};";

    public const string TenantRow = "padding:14px 0 0 0;";

    public string TenantCell => $"padding:7px 12px;background-color:{Light.Soft};border-radius:6px;font-size:13px;line-height:1.5;color:{Light.Muted};";

    public const string ButtonRow = "padding:22px 0 0 0;";

    public string ButtonCell => $"background-color:{Light.Button};border-radius:8px;text-align:center;";

    public string ButtonLink =>
        $"display:block;padding:13px 20px;background-color:{Light.Button};border-radius:8px;font-size:16px;line-height:1.5;font-weight:500;color:{Light.ButtonText};text-decoration:none;text-align:center;";

    public string Vml => $"height:46px;v-text-anchor:middle;width:{ButtonWidth}px;";

    public string VmlText => $"color:{Light.ButtonText};font-family:Microsoft JhengHei,Arial,sans-serif;font-size:16px;font-weight:500;";

    public string FallbackLabelRow => $"padding:18px 0 0 0;font-size:13px;line-height:1.6;color:{Light.Muted};";

    public const string FallbackLinkRow = "padding:8px 0 0 0;";

    public string FallbackLink =>
        $"padding:10px 12px;background-color:{Light.Soft};border-radius:6px;font-family:{options.MonospaceFontFamily};font-size:12px;line-height:1.6;color:{Light.Text};word-break:break-all;overflow-wrap:anywhere;-ms-word-break:break-all;";

    public const string NotesRow = "padding:22px 0 0 0;";

    public string Notes => $"padding:16px 0 0 0;border-top:1px solid {Light.Line};font-size:13px;line-height:1.7;color:{Light.Muted};";

    public string Footer =>
        $"padding:16px 8px 0 8px;font-family:{options.FontFamily};font-size:12px;line-height:1.6;color:{Light.Muted};text-align:center;";

    /// <summary>樣式表：深色配色與手機內距。</summary>
    public string Css => $$"""
        :root { color-scheme: light dark; supported-color-schemes: light dark; }
        body { margin: 0; padding: 0; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
        table, td { mso-table-lspace: 0; mso-table-rspace: 0; }
        @media screen and (max-width: {{Px(options.CompactBreakpoint)}}px) {
          .outer { padding: 16px 8px !important; }
          .container { padding: {{Px(options.CompactPaddingVertical)}}px {{Px(options.CompactPaddingHorizontal)}}px !important; }
        }
        @media (prefers-color-scheme: dark) {
          .bg-page { background-color: {{Dark.Page}} !important; }
          .bg-card { background-color: {{Dark.Card}} !important; }
          .bg-soft { background-color: {{Dark.Soft}} !important; }
          .tx { color: {{Dark.Text}} !important; }
          .mu { color: {{Dark.Muted}} !important; }
          .ln { border-color: {{Dark.Line}} !important; }
          .brand { color: {{Dark.Brand}} !important; }
          .btn { background-color: {{Dark.Button}} !important; color: {{Dark.ButtonText}} !important; }
        }
        """;
}