using EnvCite.ServiceDefaults;

using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>寄信的結果。寄不出去不讓動作失敗：呼叫端據此提示操作者重寄，或改用暫時密碼。</summary>
public enum MailDelivery
{
    /// <summary>沒有收件地址，沒有嘗試寄。</summary>
    NotRequested,

    /// <summary>部署者沒有啟用寄信（<c>Platform:Mail:Enabled</c>）。</summary>
    Disabled,

    /// <summary>中繼主機接受了信（只代表交給中繼，不代表對方收到）。</summary>
    Sent,

    /// <summary>連不上、逾時或被中繼拒絕。</summary>
    Failed,
}

/// <summary>寄一封 multipart/alternative 信（純文字加 HTML）。實作不丟例外（呼叫端自己的取消除外），失敗以 <see cref="MailDelivery.Failed"/> 回報。</summary>
public interface IMailSender
{
    /// <summary>是否啟用寄信。沒啟用時，建立帳號改走「暫時密碼」，由操作者親手交付。</summary>
    bool Enabled { get; }

    Task<MailDelivery> SendAsync(string recipient, string subject, MailContent content, CancellationToken cancellationToken);
}

public static class MailServiceCollectionExtensions
{
    /// <summary>
    /// 寄信所需的一切，集中在這一處註冊（Program.cs 不接任何寄信的線）：<c>Platform:Mail</c> 的三個選項類別
    /// （<see cref="MailOptions"/> 功能、<see cref="MailTransportOptions"/> 傳輸、<see cref="MailIdentityOptions"/> 寄件者，各自驗證）、
    /// <c>Platform:Mail:Template</c>（版型）、<c>Platform:Brand</c>（系統名稱）、文案的本地化、<see cref="IMailComposer"/>（組信）、
    /// <see cref="ISmtpClientFactory"/>（每封信一個新的 SMTP 用戶端）與 <see cref="IMailSender"/>（只管傳輸）。
    /// 需要 <c>AddLogging()</c>（主機本來就有）。測試可在其後換掉 <see cref="IMailSender"/> 或 <see cref="ISmtpClientFactory"/>。
    /// </summary>
    public static IServiceCollection AddPlatformMail(this IServiceCollection services)
    {
        services.AddPlatformBrand();
        services.AddLocalization();
        services.AddSectionOptions<MailOptions, MailOptionsValidator>();
        services.AddSectionOptions<MailTransportOptions, MailTransportOptionsValidator>();
        services.AddSectionOptions<MailIdentityOptions, MailIdentityOptionsValidator>()
            .PostConfigure<IOptions<MailTransportOptions>>(MailIdentityOptions.DefaultFromUsername);
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IHostedService, MailSettingsAdvisor>());
        services.AddSectionOptions<MailTemplateOptions, MailTemplateOptionsValidator>();
        services.TryAddSingleton<MailRenderServices>();
        services.TryAddSingleton(provider => new HtmlRenderer(provider.GetRequiredService<MailRenderServices>(), provider.GetRequiredService<ILoggerFactory>()));
        services.TryAddSingleton<IMailComposer, MailComposer>();
        services.TryAddSingleton<IMailTestReport, MailTestReport>();
        services.TryAddSingleton<ISmtpClientFactory, SmtpClientFactory>();
        services.TryAddSingleton<IMailSender, SmtpMailSender>();
        return services;
    }
}