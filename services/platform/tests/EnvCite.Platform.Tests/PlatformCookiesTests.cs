using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 所有 cookie scheme 共用的安全基線（<see cref="PlatformCookies"/>）：HttpOnly、SameSite=Lax、只走 HTTPS，
/// 名稱、期限、是否滑動由呼叫端決定，scheme 專屬設定在基線之後套用。
/// </summary>
public sealed class PlatformCookiesTests
{
    private static CookieAuthenticationOptions Options(string scheme, Action<CookieAuthenticationOptions>? configure = null, bool sliding = false)
    {
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddAuthentication().AddPlatformCookie(scheme, "EnvCite.Test", TimeSpan.FromMinutes(7), sliding, configure);
        using var provider = services.BuildServiceProvider();
        return provider.GetRequiredService<Microsoft.Extensions.Options.IOptionsMonitor<CookieAuthenticationOptions>>().Get(scheme);
    }

    [Fact]
    public void EveryCookieGetsTheSecurityBaseline()
    {
        var cookie = Options("a");

        Assert.True(cookie.Cookie.HttpOnly);
        Assert.Equal(SameSiteMode.Lax, cookie.Cookie.SameSite);
        Assert.Equal(CookieSecurePolicy.Always, cookie.Cookie.SecurePolicy);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void NameLifetimeAndSlidingComeFromTheCaller(bool sliding)
    {
        var cookie = Options("b", sliding: sliding);

        Assert.Equal("EnvCite.Test", cookie.Cookie.Name);
        Assert.Equal(TimeSpan.FromMinutes(7), cookie.ExpireTimeSpan);
        Assert.Equal(sliding, cookie.SlidingExpiration);
    }

    [Fact]
    public void SchemeSpecificSettingsApplyAfterTheBaseline()
    {
        var cookie = Options("c", o => o.LoginPath = "/somewhere");

        Assert.Equal("/somewhere", cookie.LoginPath.Value);
        Assert.True(cookie.Cookie.HttpOnly);
    }
}