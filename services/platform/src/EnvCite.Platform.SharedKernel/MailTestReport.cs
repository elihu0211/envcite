using System.Globalization;

using Microsoft.Extensions.Localization;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// <c>mail test</c> 給操作者看的文字：實際使用的傳輸設定與失敗時的常見原因。文案在 <see cref="MailTexts"/> 資源檔（鍵以 <c>MailTest.</c> 開頭），
/// 語言同信件（<see cref="MailTemplateOptions.Culture"/>）。<b>只印非機密的欄位</b>：主機、埠、加密模式、是否使用帳密、寄件者、回覆位址與連結來源；
/// 帳號（<see cref="MailTransportOptions.Username"/>）與密碼一律不印。
/// </summary>
public interface IMailTestReport
{
    string InvalidRecipient(string recipient);

    string NotEnabled();

    /// <summary>實際使用的設定，一行一項；帳密只說「是」或「否」，加密不足時多一行警告。</summary>
    string Settings();

    string Accepted(string recipient);

    /// <summary>失敗的說明：常見原因一次列完（含 535 與 550 5.7.60），細節在日誌，不印例外訊息（可能回顯位址）。</summary>
    string Failed(MailDelivery delivery);
}

internal sealed class MailTestReport(
    IStringLocalizer<MailTexts> texts,
    IOptions<MailTransportOptions> transport,
    IOptions<MailIdentityOptions> identity,
    IOptions<MailOptions> mail,
    IOptions<MailTemplateOptions> template) : IMailTestReport
{
    /// <summary>失敗的常見原因。文字在資源檔（<c>MailTest.Cause.{名稱}</c>）。</summary>
    private enum Cause
    {
        SourceNotTrusted,
        HostOrPort,
        SenderDomain,
        BadCredentials,
        SenderNotAccount,
    }

    /// <summary>原因與對應的 SMTP 狀態碼（沒有就是 null）：加原因只動這張表與兩份資源檔。</summary>
    private static readonly (Cause Cause, string? SmtpCode)[] Causes =
    [
        (Cause.SourceNotTrusted, null),
        (Cause.HostOrPort, null),
        (Cause.SenderDomain, null),
        (Cause.BadCredentials, "535"),
        (Cause.SenderNotAccount, "550 5.7.60"),
    ];

    public string InvalidRecipient(string recipient) => InCulture(() => T("MailTest.InvalidRecipient", recipient));

    public string NotEnabled() => InCulture(() => T("MailTest.NotEnabled"));

    public string Settings() => InCulture(() =>
    {
        var connection = transport.Value;
        var sender = identity.Value;
        var lines = new List<string>
        {
            T("MailTest.Relay", connection.Host, connection.Port),
            T("MailTest.Security", connection.Security, T($"MailTest.Security.{connection.Security}")),
            T("MailTest.Authentication", T(connection.HasCredentials ? "MailTest.Yes" : "MailTest.No")),
            T("MailTest.Sender", sender.From),
        };
        if (!string.IsNullOrWhiteSpace(sender.ReplyTo))
            lines.Add(T("MailTest.ReplyTo", sender.ReplyTo));

        lines.Add(T("MailTest.BaseUrl", mail.Value.BaseUrl));
        if (connection.SendsCredentialsInClear)
            lines.Add(T("MailTest.Warning.Clear", connection.Security));

        return string.Join('\n', lines);
    });

    public string Accepted(string recipient) => InCulture(() => T("MailTest.Accepted", recipient));

    public string Failed(MailDelivery delivery) => InCulture(() =>
    {
        var lines = new List<string> { T("MailTest.Failed", delivery) };
        lines.AddRange(Causes.Select(entry =>
        {
            var reason = T($"MailTest.Cause.{entry.Cause}");
            return "  - " + (entry.SmtpCode is null ? reason : T("MailTest.Cause.WithCode", entry.SmtpCode, reason));
        }));
        lines.Add(T("MailTest.Failed.Pointer"));
        return string.Join('\n', lines);
    });

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