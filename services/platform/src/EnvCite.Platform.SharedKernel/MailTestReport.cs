using System.Globalization;

using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// <c>mail test</c> 給操作者看的文字：實際使用的傳輸設定與失敗時的常見原因。文案在 <see cref="MailTexts"/> 資源檔（鍵以 <c>MailTest.</c> 開頭），
/// 語言同信件（<see cref="MailTemplateOptions.Culture"/>）。<b>只印非機密的欄位</b>：主機、埠、加密模式、是否使用帳密、寄件者、回覆位址與連結來源；
/// 帳號（<see cref="MailOptions.Username"/>）與密碼一律不印。
/// </summary>
public sealed class MailTestReport(IStringLocalizer<MailTexts> texts, IOptions<MailOptions> mail, IOptions<MailTemplateOptions> template)
{
    public string InvalidRecipient(string recipient) => InCulture(() => T("MailTest.InvalidRecipient", recipient));

    public string NotEnabled() => InCulture(() => T("MailTest.NotEnabled"));

    /// <summary>實際使用的設定，一行一項；帳密只說「是」或「否」。</summary>
    public string Settings() => InCulture(() =>
    {
        var settings = mail.Value;
        var lines = new List<string>
        {
            T("MailTest.Relay", settings.Host, settings.Port),
            T("MailTest.Security", settings.Security, T($"MailTest.Security.{settings.Security}")),
            T("MailTest.Authentication", T(settings.HasCredentials ? "MailTest.Yes" : "MailTest.No")),
            T("MailTest.Sender", settings.From),
        };
        if (!string.IsNullOrWhiteSpace(settings.ReplyTo))
        {
            lines.Add(T("MailTest.ReplyTo", settings.ReplyTo));
        }

        lines.Add(T("MailTest.BaseUrl", settings.BaseUrl));
        if (settings.SendsCredentialsInClear)
        {
            lines.Add(T("MailTest.Warning.Clear", settings.Security));
        }

        return string.Join('\n', lines);
    });

    public string Accepted(string recipient) => InCulture(() => T("MailTest.Accepted", recipient));

    /// <summary>失敗的說明：常見原因一次列完（含 535 與 550 5.7.60），細節在日誌，不印例外訊息（可能回顯位址）。</summary>
    public string Failed(MailDelivery delivery) => InCulture(() => T("MailTest.Failed", delivery));

    /// <summary>取文案；少了鍵就丟例外，不把鍵名印給操作者。測試守住兩個語言的鍵一致。</summary>
    private string T(string key, params object[] arguments)
    {
        var value = texts[key, arguments];
        return value.ResourceNotFound
            ? throw new InvalidOperationException($"Mail text '{key}' is missing for culture '{CultureInfo.CurrentUICulture.Name}'.")
            : value.Value;
    }

    private string InCulture(Func<string> compose) => MailCulture.Run(template.Value.Culture, compose);
}