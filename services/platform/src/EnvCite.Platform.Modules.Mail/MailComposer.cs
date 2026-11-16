using System.Globalization;
using System.Text.Encodings.Web;
using System.Text.Unicode;

using EnvCite.Platform.Modules.Mail.MailViews;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.Localization;

namespace EnvCite.Platform.Modules.Mail;

/// <summary>
/// <see cref="IMailComposer"/> 的實作。HTML 由 Razor 元件（<see cref="MailDocument"/>）經 <see cref="HtmlRenderer"/> 渲染：
/// 參數是強型別的 <see cref="MailPage"/>，所有插入的值由 Razor 自動 HTML 編碼，沒有自己拼字串的編碼點。
/// 純文字版由同一個 <see cref="MailPage"/> 與同一組已解出的文案另外產生。
/// </summary>
internal sealed class MailComposer(
    IStringLocalizer<MailTexts> texts,
    IOptions<BrandOptions> brand,
    IOptions<MailTemplateOptions> template,
    HtmlRenderer renderer) : IMailComposer
{
    public async Task<ComposedMail> ComposeActivationAsync(ActivationMailModel model, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var (subject, page, text) = InCulture(() => Activation(model));
        return new ComposedMail(subject, new MailContent(text, await RenderAsync(page)));
    }

    public async Task<ComposedMail> ComposeTestAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var (subject, page, text) = InCulture(Test);
        return new ComposedMail(subject, new MailContent(text, await RenderAsync(page)));
    }

    private (string Subject, MailPage Page, string Text) Activation(ActivationMailModel model)
    {
        var name = brand.Value.Name;
        var kind = model.Invitation ? "Invitation" : "Verification";
        var url = model.Link.AbsoluteUri;
        var period = Lifetime(model.Lifetime);
        var tenant = model.TenantIdentifier is null ? null : T("Mail.Tenant", model.TenantIdentifier);
        var validity = T("Mail.Validity", period);
        var ignore = T("Mail.Ignore");
        var heading = T($"Mail.Heading.{kind}");

        var page = Page(
            heading,
            T("Mail.Preheader", period),
            heading,
            model.Invitation ? T("Mail.Lead.Invitation", name) : T("Mail.Lead.Verification"),
            tenant,
            new MailAction(url, T($"Mail.Button.{kind}"), T("Mail.Fallback")),
            [validity, ignore]);

        // 純文字版：問候、說明、連結、租戶、期限與提示。
        var intro = model.Invitation ? T("Mail.Text.Intro.Invitation", name) : T("Mail.Text.Intro.Verification");
        var tenantLine = tenant is null ? string.Empty : $"{tenant}\n";
        var text = $"{T("Mail.Text.Greeting")}\n\n{intro}\n\n{url}\n\n{tenantLine}{validity}\n{ignore}";
        return (T($"Mail.Subject.{kind}", name), page, text);
    }

    private (string Subject, MailPage Page, string Text) Test()
    {
        var name = brand.Value.Name;
        var lead = T("Mail.Lead.Test", name);
        var note = T("Mail.Test.Note");
        var heading = T("Mail.Heading.Test");
        var page = Page(heading, T("Mail.Preheader.Test"), heading, lead, tenant: null, action: null, [note]);
        return (T("Mail.Subject.Test", name), page, $"{lead}\n{note}");
    }

    private MailPage Page(string title, string preheader, string heading, string lead, string? tenant, MailAction? action, IReadOnlyList<string> notes) =>
        new(
            T("Mail.Lang"),
            brand.Value.Name,
            $"{title} · {brand.Value.Name}",
            preheader,
            heading,
            lead,
            tenant,
            action,
            notes,
            T("Mail.Footer", brand.Value.Name),
            new MailTheme(template.Value));

    /// <summary>連結有效期限的口語寫法：一天以上用天，否則用小時（進位）。</summary>
    private string Lifetime(TimeSpan lifetime) =>
        lifetime.TotalDays >= 1
            ? T("Mail.Lifetime.Days", (int)Math.Round(lifetime.TotalDays))
            : T("Mail.Lifetime.Hours", (int)Math.Ceiling(lifetime.TotalHours));

    /// <summary>取文案；少了鍵就丟例外（不把鍵名寄給使用者）。測試守住兩個語言的鍵一致，所以實際上不會發生。</summary>
    private string T(string key, params object[] arguments)
    {
        var value = texts[key, arguments];
        return value.ResourceNotFound
            ? throw new InvalidOperationException($"Mail text '{key}' is missing for culture '{CultureInfo.CurrentUICulture.Name}'.")
            : value.Value;
    }

    /// <summary>在信件的語言（<see cref="MailTemplateOptions.Culture"/>）下取文案，見 <see cref="MailCulture"/>。</summary>
    private T InCulture<T>(Func<T> compose) => MailCulture.Run(template.Value.Culture, compose);

    private Task<string> RenderAsync(MailPage page) =>
        renderer.Dispatcher.InvokeAsync(async () =>
        {
            var parameters = ParameterView.FromDictionary(new Dictionary<string, object?> { [nameof(MailDocument.Page)] = page });
            var output = await renderer.RenderComponentAsync<MailDocument>(parameters);
            return output.ToHtmlString();
        });
}

/// <summary>
/// 給 <see cref="HtmlRenderer"/> 的專用容器，與主機的容器隔離：元件只是純排版、不注入任何服務，所以這裡只有一個 <see cref="HtmlEncoder"/>。
/// 不能直接用主機的容器：後台（Blazor）註冊了 scoped 的元件服務，渲染器從根容器解析它們會在驗證範圍的環境（Development）失敗，
/// 也不該讓後台的 UI 設定影響信件的渲染。編碼器放行 Unicode 全範圍：預設只放行基本拉丁字母，會把整封信的中文與全形標點變成
/// <c>&amp;#x…;</c>（檔案大一倍、原始碼讀不了，也更像垃圾信）；危險字元（<c>&lt; &gt; &amp; " '</c>）照樣編碼，安全性不變。
/// </summary>
internal sealed class MailRenderServices : IServiceProvider, IDisposable
{
    private readonly ServiceProvider _services = new ServiceCollection()
        .AddSingleton(HtmlEncoder.Create(UnicodeRanges.All))
        .BuildServiceProvider();

    public object? GetService(Type serviceType) => _services.GetService(serviceType);

    public void Dispose() => _services.Dispose();
}