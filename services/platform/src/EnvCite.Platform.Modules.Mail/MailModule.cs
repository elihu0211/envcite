using EnvCite.ServiceDefaults;

using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Modules.Mail;

public static class MailModule
{
    /// <summary>
    /// 寄信所需的一切，集中在這一處註冊（Program.cs 不接任何寄信的線）：<c>Platform:Mail</c> 的三個選項類別
    /// （<see cref="MailOptions"/> 功能、<see cref="MailTransportOptions"/> 傳輸、<see cref="MailIdentityOptions"/> 寄件者，各自驗證）、
    /// <c>Platform:Mail:Template</c>（版型）、<c>Platform:Brand</c>（系統名稱）、文案的本地化、<see cref="IMailComposer"/>（組信）、
    /// <see cref="ISmtpClientFactory"/>（每封信一個新的 SMTP 用戶端）與 <see cref="IMailSender"/>（只管傳輸）。
    /// 需要 <c>AddLogging()</c>（主機本來就有）。測試可在其後換掉 <see cref="IMailSender"/> 或 <see cref="ISmtpClientFactory"/>。
    /// </summary>
    public static IServiceCollection AddMailModule(this IServiceCollection services)
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