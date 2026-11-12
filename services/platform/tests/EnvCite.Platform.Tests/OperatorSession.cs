using System.Net;
using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Tests;

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
        // 建立與改旗標是兩步：不上鎖時，平行的測試會看到「已存在」就直接用，但旗標還沒改完，後台因此轉去改密碼頁。
        await EnsureLock.WaitAsync();
        try
        {
            await using var scope = app.Services.CreateAsyncScope();
            var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
            if (await users.FindByEmailAsync(email) is { } existing)
            {
                return existing;
            }

            var created = await app.Services.GetRequiredService<IOperatorAdmin>().CreateAsync(Principal(email), email, null, CancellationToken.None);
            Assert.True(created.IsOk, $"建立營運者失敗：{created.Status}");

            var user = (await users.FindByIdAsync(created.Value!.Operator.Id))!;
            user.MustChangePassword = false;
            user.TwoFactorEnabled = true;
            Assert.True((await users.UpdateAsync(user)).Succeeded);
            return user;
        }
        finally
        {
            EnsureLock.Release();
        }
    }

    private static readonly SemaphoreSlim EnsureLock = new(1, 1);

    public static ClaimsPrincipal SessionOf(this WebApplicationFactory<Program> app, OperatorUser user)
    {
        using var scope = app.Services.CreateScope();
        return scope.ServiceProvider.GetRequiredService<IUserClaimsPrincipalFactory<OperatorUser>>().CreateAsync(user).GetAwaiter().GetResult();
    }

    /// <summary>簽發一張跟真的登入一樣的票：帶絕對期限用的起點（預設就是現在，依主機的 <see cref="TimeProvider"/>）。</summary>
    public static AuthenticationProperties SessionProperties(this WebApplicationFactory<Program> app, TimeSpan? age = null)
    {
        var properties = new AuthenticationProperties();
        properties.Items[OperatorSessionLimit.StartedAtKey] = OperatorSessionLimit.Format(
            app.Services.GetRequiredService<TimeProvider>().GetUtcNow() - (age ?? TimeSpan.Zero));
        return properties;
    }

    public static string CookieFor(this WebApplicationFactory<Program> app, ClaimsPrincipal session, AuthenticationProperties? properties = null)
    {
        var cookie = app.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        return cookie.TicketDataFormat.Protect(new AuthenticationTicket(session, properties ?? app.SessionProperties(), PlatformAuth.UiCookieScheme));
    }

    /// <summary><paramref name="baseAddress"/> 是客戶端的來源（scheme 與主機）：用來驗證頁面依請求的來源組網址。</summary>
    public static HttpClient ClientFor(
        this WebApplicationFactory<Program> app, ClaimsPrincipal session, AuthenticationProperties? properties = null, Uri? baseAddress = null)
    {
        var cookie = app.Services.GetRequiredService<IOptionsMonitor<CookieAuthenticationOptions>>().Get(PlatformAuth.UiCookieScheme);
        var ticket = app.CookieFor(session, properties);
        var options = new WebApplicationFactoryClientOptions { AllowAutoRedirect = false };
        if (baseAddress is not null)
        {
            options.BaseAddress = baseAddress;
        }

        var client = app.CreateClient(options);
        client.DefaultRequestHeaders.Add("Cookie", $"{cookie.Cookie.Name}={ticket}");
        return client;
    }

    public static HttpClient SignedInClient(this WebApplicationFactory<Program> app, string email = PlatformFactory.OperatorEmail, Uri? baseAddress = null) =>
        app.ClientFor(app.SessionOf(app.EnsureOperatorAsync(email).GetAwaiter().GetResult()), baseAddress: baseAddress);

    // Blazor 會把元件參數裡的中文輸出成數字實體；瀏覽器解碼後一樣，測試也先解碼。
    public static async Task<string> PageAsync(this HttpClient client, string path)
    {
        using var response = await client.GetAsync(new Uri(path, UriKind.Relative));
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync());
    }
}