using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 所有 cookie scheme（登入、暫存憑證）共用的安全基線：HttpOnly、SameSite=Lax、只走 HTTPS。
/// 營運者與客戶入口各自註冊 scheme，基線只此一處，才不會其中一邊漏掉一項。
/// </summary>
public static class PlatformCookies
{
    /// <param name="name">cookie 名稱。</param>
    /// <param name="lifetime">有效期限；<paramref name="sliding"/> 為 true 時是滑動期限。</param>
    /// <param name="sliding">暫存憑證不滑動（到期就重來）；登入 cookie 滑動。</param>
    /// <param name="configure">基線之後再套的 scheme 專屬設定（路徑、事件）。</param>
    public static AuthenticationBuilder AddPlatformCookie(
        this AuthenticationBuilder builder,
        string scheme,
        string name,
        TimeSpan lifetime,
        bool sliding,
        Action<CookieAuthenticationOptions>? configure = null) =>
        builder.AddCookie(scheme, cookie =>
        {
            cookie.Cookie.Name = name;
            cookie.Cookie.HttpOnly = true;
            cookie.Cookie.SameSite = SameSiteMode.Lax;
            cookie.Cookie.SecurePolicy = CookieSecurePolicy.Always;
            cookie.ExpireTimeSpan = lifetime;
            cookie.SlidingExpiration = sliding;
            configure?.Invoke(cookie);
        });
}