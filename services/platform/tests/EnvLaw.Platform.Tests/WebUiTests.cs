using System.Net;
using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;

using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvLaw.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class WebUiTests(PlatformFactory factory)
{
    private HttpClient CreateUiClient() =>
        factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

    [Fact]
    public async Task UnauthenticatedUiRedirectsToLogin()
    {
        using var client = CreateUiClient();

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/login", response.Headers.Location?.AbsolutePath);
    }

    [Fact]
    public async Task LoginIsAFormWithEmailAndPasswordAndNothingElse()
    {
        using var client = CreateUiClient();

        var html = await client.PageAsync("/ui/login");

        Assert.Contains("name=\"Input.Email\"", html, StringComparison.Ordinal);
        Assert.Contains("name=\"Input.Password\"", html, StringComparison.Ordinal);
        Assert.Contains("method=\"post\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain("Google", html, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("註冊帳號", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnOperatorWithAFullSessionSeesHome()
    {
        using var client = factory.SignedInClient(PlatformFactory.OperatorEmail);

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative));
        // Blazor 會把元件參數裡的中文輸出成數字實體；瀏覽器解碼後一樣，測試也先解碼。
        var html = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync());

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("<h1 class=\"ep-page-title\">總覽</h1>", html, StringComparison.Ordinal);
        Assert.Contains("跳到主要內容", html, StringComparison.Ordinal);
        Assert.Contains("aria-label=\"主要導覽\"", html, StringComparison.Ordinal);
        Assert.Contains(PlatformFactory.OperatorEmail, html, StringComparison.Ordinal);
        Assert.True(response.Headers.CacheControl?.NoStore);
    }

    // 簽章正確的 cookie，但裡面的營運者帳號不存在（例如帳號被刪、或資料庫是另一份）：不是營運者。
    [Fact]
    public async Task ASignedSessionForAnOperatorThatDoesNotExistIsDenied()
    {
        var ghost = new ClaimsPrincipal(new ClaimsIdentity(
            [new Claim(PlatformClaimTypes.OperatorId, Guid.NewGuid().ToString()), new Claim(ClaimTypes.Email, "ghost@example.com")],
            "ghost"));
        using var client = factory.ClientFor(ghost);

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/denied", response.Headers.Location?.OriginalString);
    }

    // 只有 email、沒有營運者 Id 的 cookie（舊的 Google 登入簽的就是這種）進不來。
    [Fact]
    public async Task ASessionWithOnlyAnEmailClaimIsDenied()
    {
        using var client = factory.ClientFor(new ClaimsPrincipal(new ClaimsIdentity(
            [new Claim(ClaimTypes.Email, PlatformFactory.OperatorEmail)], "legacy")));

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/denied", response.Headers.Location?.OriginalString);
    }

    [Fact]
    public async Task DeniedPageIsPublic()
    {
        using var client = CreateUiClient();

        using var response = await client.GetAsync(new Uri("/ui/denied", UriKind.Relative));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task LogoutWithoutAntiforgeryTokenIsRejected()
    {
        using var client = factory.SignedInClient(PlatformFactory.OperatorEmail);

        using var response = await client.PostAsync(new Uri("/ui/logout", UriKind.Relative), content: null);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    // 後台掛上之後，API 與營運者 API 的驗證行為不變，且 cookie 不能冒充 API key。
    [Fact]
    public async Task UiCookieDoesNotAuthenticateApi()
    {
        using var client = factory.SignedInClient(PlatformFactory.OperatorEmail);

        using var tenantApi = await client.GetAsync(new Uri("/v2/usage", UriKind.Relative));
        using var adminApi = await client.GetAsync(new Uri("/admin/tenants/any", UriKind.Relative));
        using var operatorsApi = await client.GetAsync(new Uri("/admin/operators", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Unauthorized, tenantApi.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, adminApi.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, operatorsApi.StatusCode);
    }

    [Theory]
    [InlineData(null, "/ui")]
    [InlineData("", "/ui")]
    [InlineData("/ui/tenants?page=2", "/ui/tenants?page=2")]
    [InlineData("/ui", "/ui")]
    [InlineData("https://evil.example/ui", "/ui")]
    [InlineData("//evil.example", "/ui")]
    [InlineData("/ui//evil.example", "/ui")]
    [InlineData("/uix", "/ui")]
    [InlineData("/v2/usage", "/ui")]
    [InlineData("/ui\\evil", "/ui")]
    public void ReturnUrlStaysUnderUi(string? input, string expected) =>
        Assert.Equal(expected, UiEndpoints.SafeReturnUrl(input));
}