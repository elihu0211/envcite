using System.Text.RegularExpressions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 客戶入口幾個頁面的版面對稱：窄表單不被孤零零放在滿寬卡片的左邊。新增成員的欄位排成一列、目錄同步的兩張表單並排等寬，
/// 個人設定與綁定驗證器是置中的窄欄。版面本身由樣式表決定，這裡守住頁面用了對的版面類別。
/// </summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class PortalPageLayoutTests(PlatformFactory factory)
{
    private async Task<(string Tenant, PortalBrowser Browser)> OwnerAsync(string prefix)
    {
        var (tenant, email, initial) = await MemberAsync(factory, prefix, CustomerRoles.Owner);
        var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (tenant.Identifier, browser);
    }

    private static bool HasForm(string html, string cssClass) =>
        Regex.IsMatch(html, $"""<form[^>]*class="{Regex.Escape(cssClass)}"[^>]*>""", RegexOptions.None, TimeSpan.FromSeconds(2));

    [Fact]
    public async Task TheAddMemberFormIsOneRowOfControls()
    {
        var (tenant, browser) = await OwnerAsync("lay-members");
        using var _ = browser;

        var page = await browser.GetAsync($"/portal/{tenant}/members");

        Assert.True(HasForm(page.Html, "ep-form ep-form-row"), "the add form lays its controls out in a row");
    }

    [Fact]
    public async Task TheDirectorySettingsAndTokenFormsAreEqualGridCells()
    {
        var (tenant, browser) = await OwnerAsync("lay-directory");
        using var _ = browser;

        var page = await browser.GetAsync($"/portal/{tenant}/directory");

        var grid = page.Html.IndexOf("<div class=\"ep-grid\">", StringComparison.Ordinal);
        Assert.True(grid > 0, "settings and token sit in one grid");
        var settings = page.Html.IndexOf("id=\"settings-title\"", grid, StringComparison.Ordinal);
        var token = page.Html.IndexOf("id=\"token-title\"", grid, StringComparison.Ordinal);
        Assert.True(settings > grid && token > settings, "settings first, then the token form, both inside the grid");
        Assert.True(page.Html.IndexOf("id=\"directory\"", StringComparison.Ordinal) < grid, "the status card stays full width above the grid");
    }

    [Theory]
    [InlineData("settings")]
    [InlineData("security")]
    public async Task SingleTaskPagesAreACentredNarrowColumnWithTheirHeading(string path)
    {
        var (tenant, browser) = await OwnerAsync($"lay-{path}"[..12]);
        using var _ = browser;

        var page = await browser.GetAsync($"/portal/{tenant}/{path}");

        var column = page.Html.IndexOf("<div class=\"ep-narrow\">", StringComparison.Ordinal);
        Assert.True(column > 0, "content is in the narrow column");
        Assert.True(column < page.Html.IndexOf("class=\"ep-page-title\"", StringComparison.Ordinal), "the heading is inside the column, so the page header and the card share one axis");
    }
}