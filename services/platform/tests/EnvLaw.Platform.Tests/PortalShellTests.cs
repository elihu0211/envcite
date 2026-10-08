using System.Net;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web;
using EnvLaw.Platform.Web.Design;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

using static EnvLaw.Platform.Tests.PortalTestSupport;

namespace EnvLaw.Platform.Tests;

/// <summary>客戶入口的外殼：導覽依角色與狀態出現、目前頁有標示；版面（手機／平板／桌機）由樣式表與 token 決定。</summary>
[Collection(PlatformTestGroup.Name)]
public sealed partial class PortalShellTests(PlatformFactory factory)
{
    private static string Nav(string html)
    {
        var match = NavElement().Match(html);
        Assert.True(match.Success, "the page has no <nav class=\"ep-nav\">");
        return match.Value;
    }

    private static List<string> NavHrefs(string html) =>
        [.. NavLinkHref().Matches(Nav(html)).Select(m => m.Groups[1].Value)];

    [Theory]
    [InlineData(CustomerRoles.Owner, true)]
    [InlineData(CustomerRoles.Admin, true)]
    [InlineData(CustomerRoles.Developer, false)]
    [InlineData(CustomerRoles.Billing, false)]
    [InlineData(CustomerRoles.Viewer, false)]
    public async Task TheNavListsMembersOnlyForOwnersAndAdmins(string role, bool seesMembers)
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-a", role);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var home = await browser.GetAsync(Home(tenant.Identifier));

        var links = NavHrefs(home.Html);
        var prefix = Home(tenant.Identifier);
        Assert.Equal(seesMembers, links.Contains($"{prefix}/members"));
        Assert.Contains(prefix, links);
        Assert.Contains($"{prefix}/search", links);
        Assert.Contains($"{prefix}/security", links);
    }

    [Fact]
    public async Task TheCurrentPageIsMarkedAndTheSkipLinkHasATarget()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-b");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        var prefix = Home(tenant.Identifier);

        var home = await browser.GetAsync(prefix);
        var search = await browser.GetAsync($"{prefix}/search");

        // 總覽是完整比對：在搜尋頁它不能也亮著。
        Assert.Equal([prefix], ActiveHrefs(home.Html));
        Assert.Equal([$"{prefix}/search"], ActiveHrefs(search.Html));
        Assert.Contains("<a class=\"ep-skip-link\" href=\"#main\"", home.Html, StringComparison.Ordinal);
        Assert.Single(Regex.Matches(home.Html, "id=\"main\""));
        // 導覽取代了每頁各自的「回總覽」按鈕。
        Assert.DoesNotContain("回總覽", search.Html, StringComparison.Ordinal);
    }

    // 還在改初始密碼、註冊驗證器時只開那一頁：其他連結點了也會被導回去，所以不顯示。
    [Fact]
    public async Task ThereIsNoNavWhileAMandatoryStepIsPendingOrBeforeSignIn()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "sh-c");
        using var browser = new PortalBrowser(factory);
        var login = await browser.GetAsync(Login(tenant.Identifier));
        Assert.DoesNotContain("class=\"ep-nav\"", login.Html, StringComparison.Ordinal);

        await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial));
        var password = await browser.GetAsync(PasswordPage(tenant.Identifier));

        Assert.Equal(HttpStatusCode.OK, password.Status);
        Assert.DoesNotContain("class=\"ep-nav\"", password.Html, StringComparison.Ordinal);
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

    // 元件展示頁：只在 Development；不需要登入，三種寬度都看得到每個元件。
    [Fact]
    public async Task TheDesignGalleryExistsInDevelopmentAndIsGoneElsewhere()
    {
        using var development = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        await using var production = factory.WithWebHostBuilder(b => b.UseEnvironment("Production"));
        using var prod = production.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        var shown = await development.GetAsync(new Uri(WebModule.DesignGalleryPath, UriKind.Relative));
        var html = await shown.Content.ReadAsStringAsync();
        var hidden = await prod.GetAsync(new Uri(WebModule.DesignGalleryPath, UriKind.Relative));

        Assert.Equal(HttpStatusCode.OK, shown.StatusCode);
        Assert.Contains("class=\"ep-nav\"", html, StringComparison.Ordinal);
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

    // 版面是 CSS 的事：三種版面都在，用的是兩個斷點，導覽項高度符合觸控目標。
    [Fact]
    public void TheStylesheetLaysTheNavOutForPhoneTabletAndDesktop()
    {
        var css = File.ReadAllText(StylesheetPath());

        Assert.Matches(@"\.ep-nav\s*\{[^}]*position:\s*fixed", css); // 手機：底部分頁列
        Assert.Matches(@"@media \(width >= " + DesignTokens.Breakpoints.Tablet + @"\)\s*\{[^@]*\.ep-nav\s*\{[^}]*position:\s*sticky", css); // 平板：側邊窄欄
        Assert.Matches(@"@media \(width >= " + DesignTokens.Breakpoints.Desktop + @"\)\s*\{[^@]*\.ep-nav-link\s*\{[^}]*flex-direction:\s*row", css); // 桌機：圖示與文字並排
        Assert.Contains("env(safe-area-inset-bottom)", css, StringComparison.Ordinal); // 手機底部的安全區
        Assert.True(Rem(DesignTokens.NavItemHeight) >= Rem(DesignTokens.TouchTarget), "nav items must be at least a touch target tall");
        Assert.Contains($"--ep-nav-rail-width:{DesignTokens.NavRailWidth}", TokenCss.Render(), StringComparison.Ordinal);
    }

    // 營運後台的側欄用 MudBlazor 的 Breakpoint.Lg（1280px），也就是我們的桌機斷點。
    [Fact]
    public void TheOperatorDrawerSwitchesAtTheDesktopBreakpoint()
    {
        var layout = File.ReadAllText(Path.Combine(Path.GetDirectoryName(GalleryPath())!, "..", "Layout", "MainLayout.razor"));

        Assert.Contains("Breakpoint=\"Breakpoint.Lg\"", layout, StringComparison.Ordinal);
        Assert.Equal("80rem", DesignTokens.Breakpoints.Desktop); // 1280px；MudBlazor 的 Lg 預設也是 1280px（它的文件值，這裡沒有量測）
    }

    private static double Rem(string value) =>
        double.Parse(value.Replace("rem", string.Empty, StringComparison.Ordinal), System.Globalization.CultureInfo.InvariantCulture);

    private static List<string> ActiveHrefs(string html) =>
        [.. Anchor().Matches(Nav(html))
            .Select(m => m.Value)
            .Where(a => a.Contains("ep-nav-link active", StringComparison.Ordinal))
            .Select(a => NavLinkHref().Match(a).Groups[1].Value)];

    private static string GalleryPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvLaw.Platform.Web", "Components", "Anonymous", "DesignGallery.razor"));

    private static string StylesheetPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvLaw.Platform.Web", "wwwroot", "design", "platform.css"));

    [GeneratedRegex("<nav class=\"ep-nav\".*?</nav>", RegexOptions.Singleline)]
    private static partial Regex NavElement();

    [GeneratedRegex("<a [^>]*href=\"([^\"]+)\"")]
    private static partial Regex NavLinkHref();

    [GeneratedRegex("<a [^>]*>")]
    private static partial Regex Anchor();
}