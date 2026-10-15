using System.Net;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web;
using EnvCite.Platform.Web.Design;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 客戶入口的外殼：沒有側欄與分頁列，只有頂欄與右上角的帳號選單（依角色與狀態出現）；
/// 版面（手機／平板／桌機）由樣式表與 token 決定。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed partial class PortalShellTests(PlatformFactory factory)
{
    private static string Menu(string html)
    {
        var match = MenuElement().Match(html);
        Assert.True(match.Success, "the page has no account menu");
        return match.Value;
    }

    private static List<string> MenuHrefs(string html) =>
        [.. LinkHref().Matches(Menu(html)).Select(m => m.Groups[1].Value)];

    [Theory]
    [InlineData(CustomerRoles.Owner, true)]
    [InlineData(CustomerRoles.Admin, true)]
    [InlineData(CustomerRoles.Developer, false)]
    [InlineData(CustomerRoles.Billing, false)]
    [InlineData(CustomerRoles.Viewer, false)]
    public async Task TheMenuListsMembersOnlyForOwnersAndAdmins(string role, bool seesMembers)
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-a", role);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var home = await browser.GetAsync(Home(tenant.Identifier));

        var links = MenuHrefs(home.Html);
        var prefix = Home(tenant.Identifier);
        Assert.Equal(seesMembers, links.Contains($"{prefix}/members"));
        Assert.Equal(
            [prefix, $"{prefix}/account", $"{prefix}/security", PasswordPage(tenant.Identifier)],
            links.Where(l => l != $"{prefix}/members"));
        Assert.Contains(email, Menu(home.Html), StringComparison.Ordinal); // 選單顯示目前是誰
    }

    [Fact]
    public async Task ThereIsNoSidebarAndTheBrandLinksHome()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-b");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        var prefix = Home(tenant.Identifier);

        var home = await browser.GetAsync(prefix);
        var account = await browser.GetAsync($"{prefix}/account");

        foreach (var page in new[] { home.Html, account.Html })
        {
            Assert.DoesNotContain("class=\"ep-nav", page, StringComparison.Ordinal); // 沒有側欄、分頁列
            Assert.DoesNotContain("mud-drawer", page, StringComparison.Ordinal);
            Assert.Contains($"<a class=\"ep-brand\" href=\"{prefix}\">", page, StringComparison.Ordinal);
            Assert.Contains("<a class=\"ep-skip-link\" href=\"#main\"", page, StringComparison.Ordinal);
            Assert.Single(Regex.Matches(page, "id=\"main\""));
        }

        Assert.DoesNotContain("回總覽", account.Html, StringComparison.Ordinal);
    }

    // 還在改初始密碼、註冊驗證器時只開那一頁：其他連結點了也會被導回去，所以沒有選單，只留登出。
    [Fact]
    public async Task ThereIsNoMenuWhileAMandatoryStepIsPendingOrBeforeSignIn()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-c");
        using var browser = new PortalBrowser(factory);
        var login = await browser.GetAsync(Login(tenant.Identifier));
        Assert.DoesNotContain("ep-account-menu", login.Html, StringComparison.Ordinal);

        await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        var password = await browser.GetAsync(PasswordPage(tenant.Identifier));

        Assert.Equal(HttpStatusCode.OK, password.Status);
        Assert.DoesNotContain("ep-account-menu", password.Html, StringComparison.Ordinal);
        Assert.Contains("登出", password.Html, StringComparison.Ordinal); // 但登出一直在
    }

    [Fact]
    public async Task MemberStatesAreBadgesWithTheRightTone()
    {
        var (tenant, ownerEmail, ownerInitial) = await MemberAsync(factory, "sh-d");
        var (viewerId, _) = await AddMemberAsync(factory, tenant.Id, NewEmail(), CustomerRoles.Viewer);
        var disabled = await factory.Services.GetRequiredService<IMemberAdmin>()
            .SetDisabledAsync(OperatorSession.Principal(), tenant.Id, viewerId, true, CancellationToken.None);
        Assert.True(disabled.IsOk);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, ownerEmail, ownerInitial);

        var members = await browser.GetAsync($"{Home(tenant.Identifier)}/members");

        Assert.Contains("<span class=\"ep-badge\" data-tone=\"success\">正常</span>", members.Html, StringComparison.Ordinal);
        Assert.Contains("<span class=\"ep-badge\" data-tone=\"neutral\">已停用</span>", members.Html, StringComparison.Ordinal);
    }

    // 營運後台也沒有側欄：頂欄下方一條分頁列，目前頁標示。
    [Fact]
    public async Task TheOperatorUiHasATabStripInsteadOfADrawer()
    {
        using var operatorClient = factory.SignedInClient();

        var page = await operatorClient.PageAsync("/ui/tenants");

        Assert.DoesNotContain("class=\"mud-drawer", page, StringComparison.Ordinal); // 主題的 CSS 變數裡有 mud-drawer-*，認元件本身
        var tabs = Regex.Match(page, "<nav class=\"ep-tabs\".*?</nav>", RegexOptions.Singleline).Value;
        Assert.Equal(["/ui", "/ui/tenants", "/ui/operators", "/ui/audit"], LinkHref().Matches(tabs).Select(m => m.Groups[1].Value));
        Assert.Contains("href=\"/ui/tenants\" class=\"ep-tab active\"", tabs, StringComparison.Ordinal);
        Assert.Single(Regex.Matches(tabs, "ep-tab active"));
    }

    // 元件展示頁：只在 Development；不需要登入，三種寬度都看得到每個元件。
    [Fact]
    public async Task TheDesignGalleryExistsInDevelopmentAndIsGoneElsewhere()
    {
        using var development = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        await using var production = factory.WithWebHostBuilder(b => b.UseEnvironment("Production"));
        using var prod = production.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        var shown = await development.GetAsync(new Uri(WebModule.DesignGalleryPath, UriKind.Relative), TestContext.Current.CancellationToken);
        var html = await shown.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        var hidden = await prod.GetAsync(new Uri(WebModule.DesignGalleryPath, UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, shown.StatusCode);
        Assert.Contains("ep-account-menu", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-composer\"", html, StringComparison.Ordinal);
        foreach (var tone in new[] { "neutral", "success", "warning", "error", "info" })
        {
            Assert.Contains($"data-tone=\"{tone}\"", html, StringComparison.Ordinal);
        }

        // 圖示是真的 <path>，不是把整段 SVG 內容塞進 d 屬性（MudBlazor 的圖示常數是 SVG 內容，不是路徑資料）。
        Assert.Matches("<svg class=\"ep-icon\"[^>]*><path d=\"M[^\"]+\"", html);
        Assert.DoesNotContain("d=\"<path", html, StringComparison.Ordinal);

        Assert.Equal(HttpStatusCode.NotFound, hidden.StatusCode);
        Assert.Contains("@page \"/design\"", File.ReadAllText(GalleryPath()), StringComparison.Ordinal); // 路由與常數同步
    }

    // 版面是 CSS 的事：帳號選單是原生 popover（手機滿版、平板起靠右）；問答區的輸入框黏在底部；沒有舊的側欄樣式。
    [Fact]
    public void TheStylesheetLaysOutTheMenuAndTheAskPage()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Contains(".ep-menu:popover-open", css, StringComparison.Ordinal);
        Assert.Matches(@"@media \(width >= " + DesignTokens.Breakpoints.Tablet + @"\)\s*\{[^@]*\.ep-menu\s*\{", css); // 平板起：靠右的面板
        Assert.Matches(@"\.ep-ask\[data-state=""result""\] \.ep-composer-form\s*\{[^}]*position:\s*sticky", css); // 問了之後輸入框黏在底部
        Assert.Matches(@"@media \(width >= " + DesignTokens.Breakpoints.Desktop + @"\)\s*\{[^@]*\.ep-ask\s*\{", css); // 桌機：問答區再寬一階
        Assert.Contains("env(safe-area-inset-bottom)", css, StringComparison.Ordinal);
        Assert.DoesNotContain(".ep-nav", css, StringComparison.Ordinal); // 舊的側欄、分頁列樣式不留
        Assert.DoesNotContain("mud-drawer", css, StringComparison.Ordinal);
        // 觸控高度與輸入框共用一條規則，選單項可以是群組選擇器的第一個。
        Assert.Matches(
            @"\.ep-menu-item(?:\s*,[^{]*)?\s*\{[^}]*min-block-size:\s*var\(--ep-touch-target\)",
            css);
        // 圖示有尺寸、顏色跟著文字：少了這條，SVG 的寬度是 0、送出鈕是一顆沒有箭頭的圓。
        Assert.Matches(@"\.ep-icon\s*\{[^}]*inline-size:[^}]*fill:\s*currentColor", css);
    }

    private static string GalleryPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Components", "Anonymous", "DesignGallery.razor"));

    private static string StylesheetPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "wwwroot", "design", "platform.css"));

    [GeneratedRegex("<div id=\"ep-account-menu\".*?</div>", RegexOptions.Singleline)]
    private static partial Regex MenuElement();

    [GeneratedRegex("<a [^>]*?href=\"([^\"]+)\"")]
    private static partial Regex LinkHref();
}