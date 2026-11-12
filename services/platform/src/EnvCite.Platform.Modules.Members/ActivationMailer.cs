using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Modules.Members;

/// <summary>寄邀請信與信箱驗證信：呼叫端只給收件者與連結的資料，不碰文案、版面與傳輸。</summary>
internal interface IActivationMailer
{
    /// <summary>
    /// 沒有連結（<c>Platform:Mail:BaseUrl</c> 沒設，寄出去的連結沒用）就不寄、回 <see cref="MailDelivery.Failed"/>。
    /// 信裡只有單次、限時的連結：不放密碼，也不放使用者的其他資料。
    /// </summary>
    Task<MailDelivery> SendAsync(
        string recipient, Uri? link, TimeSpan lifetime, string? tenantIdentifier, bool invitation, CancellationToken cancellationToken);
}

/// <summary>資料流：呼叫端組資料 → <see cref="IMailComposer"/> 組成信 → <see cref="IMailSender"/> 只管傳輸。</summary>
internal sealed class ActivationMailer(IMailComposer composer, IMailSender sender) : IActivationMailer
{
    public async Task<MailDelivery> SendAsync(
        string recipient, Uri? link, TimeSpan lifetime, string? tenantIdentifier, bool invitation, CancellationToken cancellationToken)
    {
        if (link is null)
        {
            return MailDelivery.Failed;
        }

        var mail = await composer.ComposeActivationAsync(new ActivationMailModel(link, lifetime, tenantIdentifier, invitation), cancellationToken);
        return await sender.SendAsync(recipient, mail.Subject, mail.Content, cancellationToken);
    }
}

internal static class MailDeliveryExtensions
{
    /// <summary>稽核與介面記的寄信結果（只記結果，不記連結與內文）。</summary>
    public static string AuditStatus(this MailDelivery delivery) => delivery switch
    {
        MailDelivery.Sent => "sent",
        MailDelivery.Failed => "failed",
        MailDelivery.Disabled => "disabled",
        _ => "not_requested",
    };
}