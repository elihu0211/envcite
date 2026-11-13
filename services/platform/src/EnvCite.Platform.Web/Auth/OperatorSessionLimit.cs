using System.Globalization;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// 後台 session 的絕對期限（<see cref="UiOptions.SessionMaxAge"/>）。起點存在票的屬性裡、登入時寫一次：
/// 滑動續期會重寫 <c>IssuedUtc</c>，所以不能拿它當起點。沒有起點的票（不是這個流程簽的）一律拒絕。
/// </summary>
public static class OperatorSessionLimit
{
    public const string StartedAtKey = "envcite.session_started_at";

    public static string Format(DateTimeOffset at) => at.ToString("O", CultureInfo.InvariantCulture);

    /// <summary>登入簽發時寫入起點；已有就保留（改密碼等流程會用既有屬性重新登入，不該重新計時）。</summary>
    internal static Task StampAsync(CookieSigningInContext context)
    {
        context.Properties.Items.TryAdd(
            StartedAtKey, Format(context.HttpContext.RequestServices.GetRequiredService<TimeProvider>().GetUtcNow()));
        return Task.CompletedTask;
    }

    internal static async Task EnforceAsync(CookieValidatePrincipalContext context)
    {
        var services = context.HttpContext.RequestServices;
        var now = services.GetRequiredService<TimeProvider>().GetUtcNow();
        var maxAge = services.GetRequiredService<IOptions<UiOptions>>().Value.SessionMaxAge;

        if (context.Properties.Items.TryGetValue(StartedAtKey, out var raw)
            && DateTimeOffset.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var started)
            && now - started <= maxAge)
        {
            return;
        }

        context.RejectPrincipal();
        await context.HttpContext.SignOutAsync(context.Scheme.Name);
    }
}