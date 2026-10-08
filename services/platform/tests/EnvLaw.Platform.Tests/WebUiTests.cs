using System.Net;
using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;
using EnvLaw.Platform.Web.Auth;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class WebUiTests(PlatformFactory factory)
{
    private HttpClient CreateUiClient() =>
        factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

    /// <summary>不走真的 Google：用應用程式自己的 cookie 格式簽一張登入票。</summary>
    private HttpClient CreateSignedInClient(string email, bool emailVerified = true)
    {
        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [
                new Claim(ClaimTypes.Email, email),
                new Claim(OperatorAllowlist.EmailVerifiedClaim, emailVerified ? "true" : "false"),
            ],
            PlatformAuth.UiCookieScheme));
        var cookie = factory.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>()
            .Get(PlatformAuth.UiCookieScheme);
        var ticket = cookie.TicketDataFormat.Protect(new AuthenticationTicket(principal, PlatformAuth.UiCookieScheme));

        var client = CreateUiClient();
        client.DefaultRequestHeaders.Add("Cookie", $"{cookie.Cookie.Name}={ticket}");
        return client;
    }

    [Fact]
    public async Task UnauthenticatedUiRedirectsToLogin()
    {
        using var client = CreateUiClient();

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/login", response.Headers.Location?.AbsolutePath);
    }

    [Fact]
    public async Task LoginChallengesGoogle()
    {
        using var client = CreateUiClient();

        using var response = await client.GetAsync(new Uri("/ui/login", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        var location = response.Headers.Location!.ToString();
        Assert.StartsWith("https://accounts.google.com/", location, StringComparison.Ordinal);
        Assert.Contains("client_id=test-client-id", location, StringComparison.Ordinal);
        Assert.Contains("code_challenge=", location, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OperatorOnAllowlistSeesHome()
    {
        using var client = CreateSignedInClient(PlatformFactory.OperatorEmail);

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative));
        var html = await response.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("營運者後台", html, StringComparison.Ordinal);
        Assert.Contains(PlatformFactory.OperatorEmail, html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task SignedInUserNotOnAllowlistIsDenied()
    {
        using var client = CreateSignedInClient("stranger@example.com");

        using var response = await client.GetAsync(new Uri("/ui", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/denied", response.Headers.Location?.AbsolutePath);
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
        using var client = CreateSignedInClient(PlatformFactory.OperatorEmail);

        using var response = await client.PostAsync(new Uri("/ui/logout", UriKind.Relative), content: null);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    // 後台掛上之後，API 與營運者 API 的驗證行為不變，且 cookie 不能冒充 API key。
    [Fact]
    public async Task UiCookieDoesNotAuthenticateApi()
    {
        using var client = CreateSignedInClient(PlatformFactory.OperatorEmail);

        using var tenantApi = await client.GetAsync(new Uri("/v2/usage", UriKind.Relative));
        using var adminApi = await client.GetAsync(new Uri("/admin/tenants/any", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Unauthorized, tenantApi.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, adminApi.StatusCode);
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

    [Theory]
    // email, email_verified, 帳號的 hd claim, 設定的 AllowedHostedDomain, 預期
    [InlineData("operator@example.com", "true", null, null, true)]
    [InlineData("OPERATOR@Example.com", "true", null, null, true)]
    [InlineData("operator@example.com", "false", null, null, false)]
    [InlineData("operator@example.com", null, null, null, false)]
    [InlineData("other@example.com", "true", null, null, false)]
    [InlineData(null, "true", null, null, false)]
    [InlineData("operator@example.com", "true", "example.com", "example.com", true)]
    [InlineData("operator@example.com", "true", "other.com", "example.com", false)]
    [InlineData("operator@example.com", "true", null, "example.com", false)]
    public void AllowlistRequiresVerifiedEmailOnList(
        string? email, string? verified, string? hostedDomain, string? requiredDomain, bool allowed)
    {
        var claims = new List<Claim>();
        if (email is not null)
        {
            claims.Add(new Claim(ClaimTypes.Email, email));
        }

        if (verified is not null)
        {
            claims.Add(new Claim(OperatorAllowlist.EmailVerifiedClaim, verified));
        }

        if (hostedDomain is not null)
        {
            claims.Add(new Claim(OperatorAllowlist.HostedDomainClaim, hostedDomain));
        }

        var options = new UiOptions
        {
            OperatorEmails = ["operator@example.com"],
            AllowedHostedDomain = requiredDomain,
        };

        Assert.Equal(allowed, OperatorAllowlist.IsAllowed(new ClaimsPrincipal(new ClaimsIdentity(claims, "test")), options));
    }
}