using System.Net;
using System.Runtime.CompilerServices;

using EnvCite.Platform.Web;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 後台、入口與展示頁上找不到的網址要畫「找不到頁面」（維持 404），不是空白；
/// 只有 404 才畫，沒開的面與 API 維持原樣。
/// </summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class NotFoundPageTests(PlatformFactory factory)
{
    private static async Task<(HttpStatusCode Status, string Html)> GetAsync(HttpClient client, string path)
    {
        using var response = await client.GetAsync(new Uri(path, UriKind.Relative), TestContext.Current.CancellationToken);
        return (response.StatusCode, WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken)));
    }

    [Theory]
    [InlineData("/ui/no-such-page")]
    [InlineData("/ui/tenants/no/such/path")]
    [InlineData("/portal/no-such-tenant-xyz/no-such-page")]
    [InlineData("/design/no-such-page")]
    public async Task AnUnknownAddressOnAnEnabledSurfaceShowsTheNotFoundPage(string path)
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        var (status, html) = await GetAsync(client, path);

        Assert.Equal(HttpStatusCode.NotFound, status);
        Assert.Contains("<h1 class=\"ep-page-title\">找不到頁面</h1>", html, StringComparison.Ordinal);
        Assert.Contains("lang=\"zh-TW\"", html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-status-icon\"", html, StringComparison.Ordinal); // 圖示磚
        Assert.Contains("--ep-primary:", html, StringComparison.Ordinal); // 有版面與樣式，不是裸文字
    }

    [Fact]
    public async Task TheNotFoundPageSpeaksTheVisitorsLanguage()
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        client.DefaultRequestHeaders.AcceptLanguage.ParseAdd("en");

        var (status, html) = await GetAsync(client, "/ui/no-such-page");

        Assert.Equal(HttpStatusCode.NotFound, status);
        Assert.Contains("<h1 class=\"ep-page-title\">Page not found</h1>", html, StringComparison.Ordinal);
        Assert.Contains("lang=\"en\"", html, StringComparison.Ordinal);
    }

    // 空本文的 400、429 等不是「找不到」：只改 404，其他狀態碼維持原樣。
    [Fact]
    public async Task OtherEmptyErrorsAreNotTurnedIntoNotFound()
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var forged = await client.PostAsync(new Uri("/ui/logout", UriKind.Relative), new FormUrlEncodedContent([]), TestContext.Current.CancellationToken);
        var html = WebUtility.HtmlDecode(await forged.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));

        Assert.NotEqual(HttpStatusCode.NotFound, forged.StatusCode);
        Assert.DoesNotContain("找不到頁面", html, StringComparison.Ordinal);
    }

    // 沒開的面不存在：維持空的 404、不簽發任何 cookie（不讓人看出這裡曾有一個後台）。
    [Fact]
    public async Task ADisabledSurfaceStaysAnEmptyNotFoundWithoutCookies()
    {
        await using var host = factory.WithWebHostBuilder(b => b
            .UseEnvironment("Production")
            .UseSetting("Platform:Ui:Enabled", "false"));
        using var client = host.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var response = await client.GetAsync(new Uri("/ui/no-such-page", UriKind.Relative), TestContext.Current.CancellationToken);
        var html = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.Empty(html);
        Assert.False(response.Headers.Contains("Set-Cookie"));
    }

    // API 的 404 是給程式讀的，不畫網頁。
    [Fact]
    public async Task TheApiKeepsItsPlainNotFound()
    {
        using var client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        var (status, html) = await GetAsync(client, "/no-such-endpoint");

        Assert.Equal(HttpStatusCode.NotFound, status);
        Assert.DoesNotContain("找不到頁面", html, StringComparison.Ordinal);
    }

    // 路徑常數與頁面的 @page 同步：改了一邊，找不到網址時會重新執行到一個不存在的頁面。
    [Fact]
    public static void TheReExecutionPathIsThePageRoute() =>
        Assert.Contains($"@page \"{WebModule.NotFoundPath}\"", File.ReadAllText(NotFoundPagePath()), StringComparison.Ordinal);

    private static string NotFoundPagePath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Components", "Anonymous", "NotFound.razor"));
}