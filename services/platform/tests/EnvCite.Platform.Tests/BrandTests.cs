using System.Net;
using System.Text.RegularExpressions;

using EnvCite.Platform.Api;
using EnvCite.Platform.Modules.Members;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 系統名稱只有一個來源：<c>Platform:Brand:Name</c>。預設值下所有輸出與改名機制出現之前相同；
/// 設了別的名稱，介面標頭與標題、信件主旨與內文、命令列的測試信、OpenAPI 描述一起跟著變，不留一半舊名稱。
/// （寄件者顯示名稱的預設在 <see cref="SmtpMailSenderTests"/>，啟動驗證在 <see cref="ConfigurationTests"/>。）
/// </summary>
[Collection(PlatformTestGroup.Group3)]
public sealed partial class BrandTests(PlatformFactory factory)
{
    private const string Custom = "測試系統";

    private WebApplicationFactory<Program> Renamed(string name = Custom) =>
        factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Brand:Name", name));

    private static async Task<string> HtmlAsync(HttpClient client, string path, string? language)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(path, UriKind.Relative));
        if (language is not null)
            request.Headers.AcceptLanguage.ParseAdd(language);

        using var response = await client.SendAsync(request, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
    }

    private static HttpClient Client(WebApplicationFactory<Program> host, bool signedIn) =>
        signedIn ? host.SignedInClient() : host.CreateClient();

    // 標頭與頁面標題各一處：靜態渲染的登入頁、匿名頁、入口外殼（展示頁）與後台外殼。
    public static TheoryData<string, bool, string, string> Pages => new()
    {
        { "/ui/login", false, "登入 · {0}", "Sign in · {0}" },
        { "/ui/signed-out", false, "已登出 · {0}", "Signed out · {0}" },
        { "/design", false, "元件展示 · {0}", "元件展示 · {0}" },
        { "/ui", true, "總覽 · {0}", "Overview · {0}" },
        { "/ui/tenants", true, "租戶 · {0}", "Tenants · {0}" },
    };

    // 預設值下和改名機制出現之前一樣：中文介面是「環境知識」，英文介面是 EnvCite。
    [Theory]
    [MemberData(nameof(Pages))]
    public async Task TheDefaultNameReadsAsItAlwaysDid(string path, bool signedIn, string zhTitle, string enTitle)
    {
        using var client = Client(factory, signedIn);

        var zh = await HtmlAsync(client, path, "zh-TW");
        var en = await HtmlAsync(client, path, "en");

        Assert.Contains(string.Format(System.Globalization.CultureInfo.InvariantCulture, zhTitle, "環境知識"), zh, StringComparison.Ordinal);
        Assert.Contains(string.Format(System.Globalization.CultureInfo.InvariantCulture, enTitle, "EnvCite"), en, StringComparison.Ordinal);
        Assert.True(Occurrences(zh, "環境知識") >= 2, "標題與標頭都有名稱");
        Assert.DoesNotContain("環境知識", en, StringComparison.Ordinal);
    }

    // 改名後兩種語言都顯示新名稱：標題與標頭都換，舊的中文名與英文對照都不留。
    [Theory]
    [MemberData(nameof(Pages))]
    public async Task ARenamedSystemShowsTheNewNameInTheTitleAndTheHeaderInEveryLanguage(string path, bool signedIn, string zhTitle, string enTitle)
    {
        await using var host = Renamed();
        using var client = Client(host, signedIn);

        var zh = await HtmlAsync(client, path, "zh-TW");
        var en = await HtmlAsync(client, path, "en");

        Assert.Contains(string.Format(System.Globalization.CultureInfo.InvariantCulture, zhTitle, Custom), zh, StringComparison.Ordinal);
        Assert.Contains(string.Format(System.Globalization.CultureInfo.InvariantCulture, enTitle, Custom), en, StringComparison.Ordinal);
        Assert.True(Occurrences(zh, Custom) >= 2, "標題與標頭都換成新名稱");
        Assert.True(Occurrences(en, Custom) >= 2, "標題與標頭都換成新名稱");
        Assert.DoesNotContain("環境知識", zh, StringComparison.Ordinal);
        Assert.DoesNotContain("環境知識", en, StringComparison.Ordinal);
        Assert.DoesNotContain("· EnvCite", en, StringComparison.Ordinal);
        Assert.DoesNotMatch(EnglishNameAsText(), en);
    }

    [Fact]
    public async Task TheOpenApiDescriptionStartsWithTheConfiguredName()
    {
        using var defaults = factory.CreateClient();
        await using var host = Renamed();
        using var renamed = host.CreateClient();

        var before = await defaults.GetStringAsync(new Uri("/openapi/v2.json", UriKind.Relative), TestContext.Current.CancellationToken);
        var after = await renamed.GetStringAsync(new Uri("/openapi/v2.json", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Contains("\"description\": \"環境知識 API。", before, StringComparison.Ordinal);
        Assert.Contains($"\"description\": \"{Custom} API。", after, StringComparison.Ordinal);
        Assert.DoesNotContain("環境知識", after, StringComparison.Ordinal);
    }

    // 邀請信：主旨與內文（含介紹句）都帶系統名稱。預設值與改名前逐字相同。
    [Fact]
    public async Task TheInvitationToAMemberCarriesTheSystemName()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, _) = await factory.CreateTenantWithKeyAsync("brand-m");
            var byDefault = NewEmail();
            var renamed = NewEmail();
            await using var host = Renamed();

            await Invite(factory.Services, tenant.Id, byDefault);
            await Invite(host.Services, tenant.Id, renamed);

            var original = Assert.Single(factory.Mail.Sent, m => m.Recipient == byDefault);
            Assert.Equal("環境知識：邀請您啟用帳號", original.Subject);
            Assert.Contains("已為您在「環境知識」建立帳號。請開啟下列連結，設定您自己的密碼並完成信箱驗證：", original.Body, StringComparison.Ordinal);

            var changed = Assert.Single(factory.Mail.Sent, m => m.Recipient == renamed);
            Assert.Equal($"{Custom}：邀請您啟用帳號", changed.Subject);
            Assert.Contains($"已為您在「{Custom}」建立帳號。", changed.Body, StringComparison.Ordinal);
            Assert.DoesNotContain("環境知識", changed.Subject + changed.Body, StringComparison.Ordinal);
        });

        static async Task Invite(IServiceProvider services, string tenantId, string email)
        {
            var invited = await services.GetRequiredService<IMemberAdmin>()
                .InviteAsync(OperatorSession.Principal(), tenantId, email, "Ann", CustomerRoles.Viewer, CancellationToken.None);
            Assert.True(invited.IsOk, invited.Message);
        }
    }

    [Fact]
    public async Task TheInvitationToAnOperatorCarriesTheSystemName()
    {
        await factory.WithMail(async () =>
        {
            var email = NewEmail();
            await using var host = Renamed();

            var invited = await host.Services.GetRequiredService<IOperatorAdmin>()
                .InviteAsync(OperatorSession.Principal(), email, "Op", CancellationToken.None);

            Assert.True(invited.IsOk, invited.Message);
            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == email);
            Assert.Equal($"{Custom}：邀請您啟用帳號", mail.Subject);
            Assert.Contains($"「{Custom}」", mail.Body, StringComparison.Ordinal);
            Assert.DoesNotContain("環境知識", mail.Subject + mail.Body, StringComparison.Ordinal);
        });
    }

    // HTML 版的標頭、標題、說明與頁尾也都跟著名稱走，舊名稱一個字都不留。
    [Fact]
    public async Task TheHtmlOfTheInvitationFollowsTheSystemName()
    {
        await factory.WithMail(async () =>
        {
            var email = NewEmail();
            await using var host = Renamed();

            var invited = await host.Services.GetRequiredService<IOperatorAdmin>()
                .InviteAsync(OperatorSession.Principal(), email, "Op", CancellationToken.None);

            Assert.True(invited.IsOk, invited.Message);
            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == email);
            Assert.True(Occurrences(mail.Html, Custom) >= 4, "標題、標頭、說明與頁尾都有名稱");
            Assert.DoesNotContain("環境知識", mail.Html, StringComparison.Ordinal);
        });
    }

    // 驗證信（帳號已有密碼、只缺信箱驗證）與邀請信是同一個函式的兩個分支。
    [Theory]
    [InlineData(true, "環境知識", "環境知識：邀請您啟用帳號")]
    [InlineData(false, "環境知識", "環境知識：請驗證您的信箱")]
    [InlineData(true, Custom, $"{Custom}：邀請您啟用帳號")]
    [InlineData(false, Custom, $"{Custom}：請驗證您的信箱")]
    public async Task TheActivationMailSubjectFollowsTheName(bool invitation, string brand, string subject)
    {
        var sender = new CapturingMailSender();
        await using var provider = MailTestServices.Build(
            MailTestServices.With(("Platform:Brand:Name", brand)),
            services =>
            {
                services.AddSingleton<IMailSender>(sender);
                services.AddSingleton<IActivationMailer, ActivationMailer>();
            });

        await provider.GetRequiredService<IActivationMailer>().SendAsync(
            "someone@example.test", new Uri("https://platform.test/x"), TimeSpan.FromDays(3), "acme", invitation, CancellationToken.None);

        var mail = Assert.Single(sender.Sent);
        Assert.Equal(subject, mail.Subject);
        if (invitation)
            Assert.Contains($"已為您在「{brand}」建立帳號", mail.Body, StringComparison.Ordinal);
    }

    private async Task<(int Code, string Output, string Mail, string Subject)> MailTestAsync(string? brand)
    {
        var database = await factory.CreateDatabaseAsync();
        await using var host = factory.WithWebHostBuilder(builder =>
        {
            builder.UseEnvironment("Production");
            builder.UseSetting("ConnectionStrings:Platform", database);
            if (brand is not null)
                builder.UseSetting("Platform:Brand:Name", brand);
        });
        var to = $"probe-{Guid.NewGuid():N}@example.test";
        (int Code, string Output, string Mail, string Subject) result = default;
        await factory.WithMail(async () =>
        {
            var output = new StringWriter();
            var code = await OperatorCli.RunAsync(host.Services, ["mail", "test", to], output, new StringWriter(), CancellationToken.None);
            var sent = Assert.Single(factory.Mail.Sent, m => m.Recipient == to);
            result = (code, output.ToString(), sent.Body, sent.Subject);
        });
        return result;
    }

    [Fact]
    public async Task TheTestMailOfTheCommandLineKeepsTheOriginalWordingByDefault()
    {
        var (code, _, body, subject) = await MailTestAsync(brand: null);

        Assert.Equal(0, code);
        Assert.Equal("環境知識：寄信測試", subject);
        Assert.Equal("這是一封測試信，用來確認「環境知識」的寄信設定與中繼可用。\n沒有連結，也沒有密碼；收到後不需要做任何事。", body);
    }

    [Fact]
    public async Task TheTestMailOfTheCommandLineFollowsTheName()
    {
        var (code, _, body, subject) = await MailTestAsync(Custom);

        Assert.Equal(0, code);
        Assert.Equal($"{Custom}：寄信測試", subject);
        Assert.Equal($"這是一封測試信，用來確認「{Custom}」的寄信設定與中繼可用。\n沒有連結，也沒有密碼；收到後不需要做任何事。", body);
    }

    private static int Occurrences(string text, string value) =>
        text.Split(value, StringSplitOptions.None).Length - 1;

    // 英文名稱單獨成為一段文字（標頭），不是 CSS 類別或資源路徑裡的 EnvCite.Platform.Web。
    [GeneratedRegex(@">\s*EnvCite\s*<", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex EnglishNameAsText();
}