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

/// <summary>以營運者身分打營運後台：直接用主機的 cookie 設定簽一張登入票，不經過 Google。</summary>
internal static class OperatorSession
{
    public static ClaimsPrincipal Principal(string email = PlatformFactory.OperatorEmail) => new(new ClaimsIdentity(
        [new Claim(ClaimTypes.Email, email), new Claim(OperatorAllowlist.EmailVerifiedClaim, "true")],
        PlatformAuth.UiCookieScheme));

    public static HttpClient SignedInClient(this WebApplicationFactory<Program> app, string email = PlatformFactory.OperatorEmail)
    {
        var cookie = app.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        var ticket = cookie.TicketDataFormat.Protect(new AuthenticationTicket(Principal(email), PlatformAuth.UiCookieScheme));
        var client = app.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        client.DefaultRequestHeaders.Add("Cookie", $"{cookie.Cookie.Name}={ticket}");
        return client;
    }

    // Blazor 會把元件參數裡的中文輸出成數字實體；瀏覽器解碼後一樣，測試也先解碼。
    public static async Task<string> PageAsync(this HttpClient client, string path)
    {
        using var response = await client.GetAsync(new Uri(path, UriKind.Relative));
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync());
    }
}