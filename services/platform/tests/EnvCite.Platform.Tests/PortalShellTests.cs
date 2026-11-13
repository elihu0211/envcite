using System.Net;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

using EnvCite.Platform.Web;
using EnvCite.Platform.Web.Design;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 客戶入口的外殼：頂欄加左側欄（工作區），個人設定在右上角的帳號選單，兩者都依角色與狀態出現；
/// 版面（手機／平板／桌機）由樣式表與 token 決定。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
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

    private static List<string> SideNavHrefs(string html) =>
        [.. Regex.Matches(html, "<nav class=\"ep-side-nav\".*?</nav>", RegexOptions.Singleline)
            .SelectMany(nav => LinkHref().Matches(nav.Value)).Select(m => m.Groups[1].Value)];

    [Theory]
    [InlineData(CustomerRoles.Owner, true)]
    [InlineData(CustomerRoles.Admin, true)]
    [InlineData(CustomerRoles.Developer, false)]
    [InlineData(CustomerRoles.Billing, false)]
    [InlineData(CustomerRoles.Viewer, false)]
    public async Task TheSideNavigationListsMembersOnlyForOwnersAndAdmins(string role, bool seesMembers)
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-a", role);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var home = await browser.GetAsync(Home(tenant.Identifier));

        var prefix = Home(tenant.Identifier);
        var links = SideNavHrefs(home.Html);
        Assert.Equal(seesMembers, links.Contains($"{prefix}/members"));

        // 目錄同步（共用的測試主機開著）只列給擁有者：設定與 token 只有擁有者能管。
        Assert.Equal(role == CustomerRoles.Owner, links.Contains($"{prefix}/directory"));
        Assert.Equal([prefix, $"{prefix}/account"], links.Where(l => l != $"{prefix}/members" && l != $"{prefix}/history" && l != $"{prefix}/directory"));
        // 個人設定（外觀）與個人的安全設定在帳號選單；選單也顯示目前是誰。
        Assert.Equal([$"{prefix}/settings", $"{prefix}/security", PasswordPage(tenant.Identifier)], MenuHrefs(home.Html));
        Assert.Contains(email, Menu(home.Html), StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheShellHasASideNavigationAndTheBrandLinksHome()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-b");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        var prefix = Home(tenant.Identifier);

        var home = await browser.GetAsync(prefix);
        var account = await browser.GetAsync($"{prefix}/account");

        foreach (var page in new[] { home.Html, account.Html })
        {
            Assert.DoesNotContain("mud-drawer", page, StringComparison.Ordinal);
            Assert.Contains($"<a class=\"ep-brand\" href=\"{prefix}\">", page, StringComparison.Ordinal);
            Assert.Contains("<a class=\"ep-skip-link\" href=\"#main\"", page, StringComparison.Ordinal);
            Assert.Single(Regex.Matches(page, "id=\"main\""));
        }

        // 目前頁標示：首頁只在首頁算數，不因為其他頁都在它底下就一直亮著。
        Assert.Single(Regex.Matches(account.Html, "ep-side-link active"));
        Assert.Contains($"href=\"{prefix}/account\" class=\"ep-side-link active\"", account.Html, StringComparison.Ordinal);
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

    // 營運後台是頂欄加自己的側欄（不是 MudBlazor 的抽屜）：只列存在的頁面，目前頁標示。
    [Fact]
    public async Task TheOperatorUiHasASideNavigationOfExistingPages()
    {
        using var operatorClient = factory.SignedInClient();

        var page = await operatorClient.PageAsync("/ui/tenants");

        Assert.DoesNotContain("class=\"mud-drawer", page, StringComparison.Ordinal); // 主題的 CSS 變數裡有 mud-drawer-*，認元件本身
        var nav = Regex.Match(page, "<nav class=\"ep-side-nav\".*?</nav>", RegexOptions.Singleline).Value;
        Assert.Equal(["/ui", "/ui/tenants", "/ui/operators", "/ui/audit"], LinkHref().Matches(nav).Select(m => m.Groups[1].Value));
        Assert.Contains("href=\"/ui/tenants\" class=\"ep-side-link active\"", nav, StringComparison.Ordinal);
        Assert.Single(Regex.Matches(nav, "ep-side-link active"));

        // 每一項是圖示加文字（窄欄時只剩圖示、文字留給輔助技術），頂欄有導覽鈕、後面有抽屜的遮罩。
        Assert.Equal(4, Regex.Count(nav, "<svg class=\"ep-icon\""));
        Assert.Equal(4, Regex.Count(nav, "class=\"ep-side-label\""));
        Assert.Contains("class=\"ep-side-toggle\" aria-controls=\"ep-side\"", page, StringComparison.Ordinal);
        Assert.Contains("<aside id=\"ep-side\" class=\"ep-side\">", page, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-scrim\"", page, StringComparison.Ordinal);
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
            Assert.Contains($"data-tone=\"{tone}\"", html, StringComparison.Ordinal);

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