using System.Net;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 共用模板輸出的標記：頁面不再自己寫它們（見 <see cref="ComponentTemplateTests"/>），所以這裡鎖住模板的輸出，
/// 改標記時所有頁面一起變、也一起被測到。用元件展示頁（<c>/design</c>）當樣本：每個模板在那裡至少用一次。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class SharedTemplateOutputTests(PlatformFactory factory)
{
    private async Task<string> GalleryAsync()
    {
        using var client = factory.CreateClient();
        using var response = await client.GetAsync(new Uri("/design", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task ASectionCardLabelsItselfByItsHeading()
    {
        var html = await GalleryAsync();

        Assert.Contains("<section class=\"ep-card\" aria-labelledby=\"g-color\">", html, StringComparison.Ordinal);
        Assert.Contains("<h2 id=\"g-color\" class=\"ep-section-title\">", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ATableCardCarriesItsExtraClassesOnTheCardAndTheHeading()
    {
        var html = await GalleryAsync();

        Assert.Contains("<section class=\"ep-card ep-table-card ep-cards\" aria-labelledby=\"g-table\">", html, StringComparison.Ordinal);
        Assert.Contains("<h2 id=\"g-table\" class=\"ep-section-title ep-card-title\">", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ACardWithoutAHeadingHasNoDanglingLabel()
    {
        var html = await GalleryAsync();

        Assert.DoesNotContain("aria-labelledby=\"g-empty\"", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AStatGridRendersLinkTilesAndPlainTiles()
    {
        var html = await GalleryAsync();

        Assert.Contains("<section class=\"ep-stats\" aria-label=\"範例摘要\">", html, StringComparison.Ordinal);
        Assert.Contains("<a class=\"ep-stat\" href=\"/design\">", html, StringComparison.Ordinal);
        Assert.Contains("<div class=\"ep-stat\">", html, StringComparison.Ordinal);
        Assert.Contains("<span class=\"ep-stat-value\">3,408</span>", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ADataTableLabelsEveryCellForTheNarrowCardLayoutExceptTheLeadColumn()
    {
        var html = await GalleryAsync();

        Assert.Contains("<table class=\"ep-plain-table\" aria-label=\"範例\">", html, StringComparison.Ordinal);
        Assert.Contains("<th scope=\"col\">", html, StringComparison.Ordinal);
        Assert.Contains("<td data-label=\"角色\">", html, StringComparison.Ordinal);
        Assert.Contains("<td data-label=\"狀態\">", html, StringComparison.Ordinal);
        Assert.Contains("<td>owner@example.test</td>", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnOptionSelectIsANativeSelectNamedForThePostedForm()
    {
        var html = await GalleryAsync();

        Assert.Contains("<select id=\"g-role\" name=\"g-role\" class=\"ep-input\" aria-label=\"角色\">", html, StringComparison.Ordinal);
        Assert.Contains("<option value=\"developer\">開發者</option>", html, StringComparison.Ordinal);
    }
}