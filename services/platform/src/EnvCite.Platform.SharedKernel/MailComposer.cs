using System.Globalization;
using System.Text.Encodings.Web;
using System.Text.Unicode;

using EnvCite.Platform.SharedKernel.MailViews;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>一封信的內容：純文字版與 HTML 版，語意相同（同一條連結、同樣的期限與提示），以 multipart/alternative 寄出。</summary>
public sealed record MailContent(string Text, string Html);

/// <summary>寫好、可以寄的信：主旨加內容。收件者由呼叫端決定，傳輸層（<see cref="IMailSender"/>）只管寄。</summary>
public sealed record ComposedMail(string Subject, MailContent Content);

/// <summary>邀請（<see cref="Invitation"/>）或信箱驗證信要的資料：呼叫端只組這個，不碰文案與版面。</summary>
/// <param name="Link">單次、限時的啟用連結；信裡唯一的外部網址。</param>
/// <param name="Lifetime">連結的有效期限（一段時間，不是時間點）。</param>
/// <param name="TenantIdentifier">租戶識別碼；營運者沒有租戶，為 null。</param>
/// <param name="Invitation">邀請（帳號還沒有密碼）為 true，驗證信箱為 false。</param>
public sealed record ActivationMailModel(Uri Link, TimeSpan Lifetime, string? TenantIdentifier, bool Invitation);

/// <summary>
/// 把資料組成信：文案（<see cref="MailTexts"/> 資源檔，語言由 <see cref="MailTemplateOptions.Culture"/> 決定）、系統名稱（<see cref="BrandOptions"/>）
/// 與版型（<see cref="MailTemplateOptions"/>）合成主旨、純文字與 HTML。<b>不寄信、不連網路</b>，所以預覽命令與測試用的是同一個服務。
/// </summary>
public interface IMailComposer
{
    /// <summary>邀請信或信箱驗證信。信裡只有連結：不放密碼，也不放使用者的其他資料。</summary>
    Task<ComposedMail> ComposeActivationAsync(ActivationMailModel model, CancellationToken cancellationToken);

    /// <summary><c>mail test</c> 的測試信：沒有連結、沒有密碼，只確認寄信設定與中繼可用。</summary>
    Task<ComposedMail> ComposeTestAsync(CancellationToken cancellationToken);
}

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

    /// <summary>
    /// 在信件的語言（<see cref="MailTemplateOptions.Culture"/>）下取文案。只包住同步的取字串，不跨 await，所以暫時換掉目前執行緒的文化不會影響別的工作。
    /// </summary>
    private T InCulture<T>(Func<T> compose)
    {
        var culture = CultureInfo.GetCultureInfo(template.Value.Culture);
        var (current, currentUi) = (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture);
        (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture) = (culture, culture);
        try
        {
            return compose();
        }
        finally
        {
            (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture) = (current, currentUi);
        }
    }

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