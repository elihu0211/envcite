using System.Globalization;
using System.Net;
using System.Text;

namespace EnvCite.Platform.SharedKernel;

/// <summary>一封信的內容：純文字版與 HTML 版，語意相同（同一條連結、同樣的期限與提示），以 multipart/alternative 寄出。</summary>
public sealed record MailContent(string Text, string Html);

/// <summary>
/// 邀請信、驗證信與 <c>mail test</c> 共用的版型。版型寫在程式裡（不是內嵌資源檔）：每個插入點都經同一個 <see cref="Encode"/>，
/// 沒有「樣板變數替換」這一層，所以不會有漏編碼的占位符；有無租戶、有無按鈕這類分支也在編譯期就檢查。
/// <para>
/// 安全限制（有測試）：信裡沒有遠端圖片、追蹤像素、點擊追蹤轉址或任何腳本，唯一的外部網址就是啟用連結——
/// 它等同於一次登入的鑰匙，不能經第三方，也不能被信件客戶端預先載入。所有插入的值都經 HTML 編碼。
/// </para>
/// <para>
/// 相容性：版面是表格加內嵌樣式（傳統 Outlook 用 Word 的 HTML 引擎，不支援 flexbox、grid，也不認 max-width），
/// 600px 以 <c>max-width</c> 為主、Outlook 用條件註解包一張固定寬度的表格；按鈕是「橋接按鈕」（padding 在連結上，整塊可點），
/// Outlook 改用 VML <c>v:roundrect</c>。深色配色只在支援 <c>prefers-color-scheme</c> 的客戶端套用，其餘客戶端維持淺色。
/// </para>
/// </summary>
public static class MailTemplate
{
    /// <summary>一組配色。淺色與深色各一組，樣式表與內嵌樣式都由它產生，測試用同一份數值驗證對比度。</summary>
    public sealed record Palette(
        string Page, string Card, string Text, string Muted, string Line, string Soft, string Brand, string Button, string ButtonText);

    public static readonly Palette Light = new("#eef0f2", "#ffffff", "#1f2328", "#59636e", "#d8dde2", "#f3f5f7", "#185fa5", "#185fa5", "#ffffff");

    public static readonly Palette Dark = new("#16191d", "#1f242a", "#e6e9ec", "#a3adb8", "#343b43", "#2a3038", "#85b7eb", "#85b7eb", "#042c53");

    private const string Sans = "'PingFang TC','Microsoft JhengHei','Noto Sans TC',system-ui,sans-serif";

    private const string Mono = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Courier New',monospace";

    /// <summary>「如果您沒有預期收到這封信」的提示：純文字版與 HTML 版逐字相同。</summary>
    public const string IgnoreNotice = "如果您沒有預期收到這封信，請忽略它：在您開啟連結之前，不會有任何變更。";

    /// <summary>邀請（<paramref name="invitation"/>）或信箱驗證信。信裡只有單次、限時的連結：不放密碼，也不放使用者的其他資料。</summary>
    public static MailContent Activation(string brand, Uri link, TimeSpan lifetime, string? tenantIdentifier, bool invitation)
    {
        var url = link.AbsoluteUri;
        var period = Lifetime(lifetime);
        var tenantLine = tenantIdentifier is null ? string.Empty : $"租戶識別碼：{tenantIdentifier}\n";
        var intro = invitation
            ? $"已為您在「{brand}」建立帳號。請開啟下列連結，設定您自己的密碼並完成信箱驗證："
            : "請開啟下列連結，驗證這個信箱屬於您：";
        var text = $"您好，\n\n{intro}\n\n{url}\n\n{tenantLine}連結 {period}內有效，而且只能使用一次。\n{IgnoreNotice}";

        var heading = invitation ? "邀請您啟用帳號" : "請驗證您的信箱";
        var lead = invitation
            ? $"已為您在「{brand}」建立帳號。請按下方按鈕，設定您自己的密碼並完成信箱驗證。"
            : "請按下方按鈕，驗證這個信箱屬於您。";
        var rows = new StringBuilder()
            .Append(HeadingRows(heading, lead));
        if (tenantIdentifier is not null)
        {
            rows.Append(Row("padding:14px 0 0 0;", Soft($"租戶識別碼：{Encode(tenantIdentifier)}")));
        }

        rows.Append(Row("padding:22px 0 0 0;", Button(url, invitation ? "設定密碼並啟用帳號" : "驗證信箱")))
            .Append(Row("padding:18px 0 0 0;font-size:13px;line-height:1.6;", "按鈕無法點選時，請複製下列連結到瀏覽器：", "mu", Light.Muted))
            .Append(Row("padding:8px 0 0 0;", $"""
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="bg-soft tx" bgcolor="{Light.Soft}" style="padding:10px 12px;background-color:{Light.Soft};border-radius:6px;font-family:{Mono};font-size:12px;line-height:1.6;color:{Light.Text};word-break:break-all;overflow-wrap:anywhere;-ms-word-break:break-all;">{Encode(url)}</td></tr></table>
                """))
            .Append(Notes($"連結 {Encode(period)}內有效，而且只能使用一次。<br>{Encode(IgnoreNotice)}"));

        return new MailContent(text, Document(brand, heading, $"連結 {period}內有效，只能使用一次", rows.ToString()));
    }

    /// <summary>
    /// <c>mail test</c> 的測試信：沒有連結、沒有密碼，只確認寄信設定與中繼可用。純文字版逐字維持原本的措辭。
    /// </summary>
    public static MailContent Test(string brand)
    {
        const string Tail = "沒有連結，也沒有密碼；收到後不需要做任何事。";
        var lead = $"這是一封測試信，用來確認「{brand}」的寄信設定與中繼可用。";
        var rows = new StringBuilder()
            .Append(HeadingRows("寄信測試", lead))
            .Append(Notes(Encode(Tail)));
        return new MailContent($"{lead}\n{Tail}", Document(brand, "寄信測試", "這是寄信設定的測試信，收到後不需要做任何事", rows.ToString()));
    }

    /// <summary>連結有效期限的口語寫法：一天以上用天，否則用小時（進位）。</summary>
    public static string Lifetime(TimeSpan lifetime) =>
        lifetime.TotalDays >= 1
            ? string.Create(CultureInfo.InvariantCulture, $"{(int)Math.Round(lifetime.TotalDays)} 天")
            : string.Create(CultureInfo.InvariantCulture, $"{(int)Math.Ceiling(lifetime.TotalHours)} 小時");

    /// <summary>HTML 編碼；放在元素內容與以雙引號或單引號包起來的屬性值都安全。</summary>
    private static string Encode(string value) => WebUtility.HtmlEncode(value);

    private static string Row(string style, string content, string? cssClass = null, string? color = null)
    {
        var classAttribute = cssClass is null ? string.Empty : $" class=\"{cssClass}\"";
        var colorStyle = color is null ? string.Empty : $"color:{color};";
        return $"<tr><td{classAttribute} style=\"{style}{colorStyle}\">{content}</td></tr>\n";
    }

    private static string HeadingRows(string heading, string lead) =>
        Row("padding:22px 0 0 0;", $"""<h1 class="tx" style="margin:0;font-size:21px;line-height:1.4;font-weight:500;color:{Light.Text};">{Encode(heading)}</h1>""")
        + Row("padding:10px 0 0 0;font-size:15px;line-height:1.7;", Encode(lead), "tx", Light.Text);

    private static string Soft(string encodedContent) =>
        $"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="bg-soft mu" bgcolor="{Light.Soft}" style="padding:7px 12px;background-color:{Light.Soft};border-radius:6px;font-size:13px;line-height:1.5;color:{Light.Muted};">{encodedContent}</td></tr></table>""";

    private static string Notes(string encodedContent) =>
        Row("padding:22px 0 0 0;", $"""
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="ln mu" style="padding:16px 0 0 0;border-top:1px solid {Light.Line};font-size:13px;line-height:1.7;color:{Light.Muted};">{encodedContent}</td></tr></table>
            """);

    /// <summary>
    /// 橋接按鈕：背景在儲存格、padding 在連結上，整塊都能點；Outlook（Word 引擎）不畫圓角與連結的 padding，
    /// 所以改用 VML 圓角矩形（寬度是 600px 版面扣掉左右內距 32px 後的 536px，與條件註解裡固定寬度的表格對應）。
    /// </summary>
    private static string Button(string url, string label)
    {
        var href = Encode(url);
        var text = Encode(label);
        return $"""
            <!--[if mso]>
            <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{href}" style="height:46px;v-text-anchor:middle;width:536px;" arcsize="17%" strokecolor="{Light.Button}" fillcolor="{Light.Button}">
            <w:anchorlock/>
            <center style="color:{Light.ButtonText};font-family:'Microsoft JhengHei',Arial,sans-serif;font-size:16px;font-weight:500;">{text}</center>
            </v:roundrect>
            <![endif]-->
            <!--[if !mso]><!-->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="btn" bgcolor="{Light.Button}" style="background-color:{Light.Button};border-radius:8px;text-align:center;"><a class="btn" href="{href}" target="_blank" rel="noopener noreferrer" style="display:block;padding:13px 20px;background-color:{Light.Button};border-radius:8px;font-size:16px;line-height:1.5;font-weight:500;color:{Light.ButtonText};text-decoration:none;text-align:center;">{text}</a></td></tr></table>
            <!--<![endif]-->
            """;
    }

    private static string Document(string brand, string heading, string preheader, string rows)
    {
        var brandName = Encode(brand);
        var dark = Dark;
        var light = Light;
        // 預覽文字後面補不可見的空白，避免客戶端在收件匣預覽裡接著顯示內文的開頭。
        var filler = string.Concat(Enumerable.Repeat("&zwnj;&nbsp;", 60));
        return $$"""
            <!DOCTYPE html>
            <html lang="zh-Hant" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
            <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <meta name="x-apple-disable-message-reformatting">
            <meta name="format-detection" content="telephone=no, date=no, address=no, email=no">
            <meta name="color-scheme" content="light dark">
            <meta name="supported-color-schemes" content="light dark">
            <title>{{Encode(heading)}} · {{brandName}}</title>
            <!--[if mso]>
            <noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
            <style>table, td, h1, p, a, center { font-family: 'Microsoft JhengHei', Arial, sans-serif !important; }</style>
            <![endif]-->
            <style>
            :root { color-scheme: light dark; supported-color-schemes: light dark; }
            body { margin: 0; padding: 0; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
            table, td { mso-table-lspace: 0; mso-table-rspace: 0; }
            @media screen and (max-width: 480px) {
              .outer { padding: 16px 8px !important; }
              .container { padding: 22px 18px !important; }
            }
            @media (prefers-color-scheme: dark) {
              .bg-page { background-color: {{dark.Page}} !important; }
              .bg-card { background-color: {{dark.Card}} !important; }
              .bg-soft { background-color: {{dark.Soft}} !important; }
              .tx { color: {{dark.Text}} !important; }
              .mu { color: {{dark.Muted}} !important; }
              .ln { border-color: {{dark.Line}} !important; }
              .brand { color: {{dark.Brand}} !important; }
              .btn { background-color: {{dark.Button}} !important; color: {{dark.ButtonText}} !important; }
            }
            </style>
            </head>
            <body class="bg-page" style="margin:0;padding:0;background-color:{{light.Page}};">
            <div style="display:none;visibility:hidden;opacity:0;color:transparent;height:0;width:0;max-height:0;max-width:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;">{{Encode(preheader)}}{{filler}}</div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="bg-page" bgcolor="{{light.Page}}" style="background-color:{{light.Page}};">
            <tr><td align="center" class="outer" style="padding:24px 12px;">
            <!--[if mso]><table role="presentation" align="center" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td width="600"><![endif]-->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;">
            <tr><td class="bg-card container tx" bgcolor="{{light.Card}}" style="padding:28px 32px;background-color:{{light.Card}};border-radius:12px;font-family:{{Sans}};color:{{light.Text}};">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr><td class="brand ln" style="padding:0 0 14px 0;border-bottom:1px solid {{light.Line}};font-size:14px;line-height:1.4;font-weight:500;letter-spacing:.08em;color:{{light.Brand}};">{{brandName}}</td></tr>
            {{rows}}</table>
            </td></tr>
            <tr><td class="mu" align="center" style="padding:16px 8px 0 8px;font-family:{{Sans}};font-size:12px;line-height:1.6;color:{{light.Muted}};text-align:center;">這是系統自動寄出的信 · {{brandName}}</td></tr>
            </table>
            <!--[if mso]></td></tr></table><![endif]-->
            </td></tr>
            </table>
            </body>
            </html>
            """;
    }
}