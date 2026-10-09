using System.Net;
using System.Security.Claims;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// 以營運者身分打營運後台。<see cref="SignedInClient"/> 不走登入頁：在資料庫建立一位已改過密碼、已註冊驗證器的營運者
/// （一個 email 只建一次），再用主機自己的 cookie 設定與 claims factory 簽一張跟真的登入一樣的票。
/// 登入流程本身由 <c>OperatorSignInTests</c> 走真的表單測。
/// </summary>
internal static class OperatorSession
{
    /// <summary>稽核裡的操作者（<c>operator-user</c>，以 email 辨認）：給服務的 <c>actor</c> 參數用，不是登入。</summary>
    public static ClaimsPrincipal Principal(string email = PlatformFactory.OperatorEmail) =>
        new(new ClaimsIdentity([new Claim(ClaimTypes.Email, email)], "test-operator"));

    /// <summary>已完成所有強制步驟的營運者帳號（沒有就建立）。</summary>
    public static async Task<OperatorUser> EnsureOperatorAsync(this WebApplicationFactory<Program> app, string email = PlatformFactory.OperatorEmail)
    {
        await using var scope = app.Services.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindByEmailAsync(email) is { } existing)
        {
            return existing;
        }

        var created = await app.Services.GetRequiredService<IOperatorAdmin>().CreateAsync(Principal(email), email, null, CancellationToken.None);
        if (!created.IsOk)
        {
            // 平行的測試剛好同時建了同一位：以資料庫裡的為準。
            Assert.Equal(AdminStatus.Conflict, created.Status);
            return (await users.FindByEmailAsync(email))!;
        }

        var user = (await users.FindByIdAsync(created.Value!.Operator.Id))!;
        user.MustChangePassword = false;
        user.TwoFactorEnabled = true;
        Assert.True((await users.UpdateAsync(user)).Succeeded);
        return user;
    }

    public static ClaimsPrincipal SessionOf(this WebApplicationFactory<Program> app, OperatorUser user)
    {
        using var scope = app.Services.CreateScope();
        return scope.ServiceProvider.GetRequiredService<IUserClaimsPrincipalFactory<OperatorUser>>().CreateAsync(user).GetAwaiter().GetResult();
    }

    public static HttpClient ClientFor(this WebApplicationFactory<Program> app, ClaimsPrincipal session)
    {
        var cookie = app.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        var ticket = cookie.TicketDataFormat.Protect(new AuthenticationTicket(session, PlatformAuth.UiCookieScheme));
        var client = app.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        client.DefaultRequestHeaders.Add("Cookie", $"{cookie.Cookie.Name}={ticket}");
        return client;
    }

    public static HttpClient SignedInClient(this WebApplicationFactory<Program> app, string email = PlatformFactory.OperatorEmail) =>
        app.ClientFor(app.SessionOf(app.EnsureOperatorAsync(email).GetAwaiter().GetResult()));

    // Blazor 會把元件參數裡的中文輸出成數字實體；瀏覽器解碼後一樣，測試也先解碼。
    public static async Task<string> PageAsync(this HttpClient client, string path)
    {
        using var response = await client.GetAsync(new Uri(path, UriKind.Relative));
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync());
    }
}