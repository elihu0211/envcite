using System.Security.Claims;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 後台 session 的 cookie 裡放什麼：營運者 Id、email、顯示名稱、Identity 的安全戳記，以及「還必須先改密碼／先註冊驗證器」。
/// 授權<b>不信</b>這些 claim 的狀態：每個請求由 <see cref="OperatorSessions"/> 重查資料庫（claim 只用來決定被拒時導去哪一頁）。
/// </summary>
internal sealed class OperatorClaimsPrincipalFactory(UserManager<OperatorUser> users, IOptions<IdentityOptions> options)
    : UserClaimsPrincipalFactory<OperatorUser>(users, options)
{
    protected override async Task<ClaimsIdentity> GenerateClaimsAsync(OperatorUser user)
    {
        // 基底已加上 NameIdentifier、Name（= email）、Email 與安全戳記。
        var identity = await base.GenerateClaimsAsync(user);
        identity.AddClaim(new Claim(PlatformClaimTypes.OperatorId, user.Id.ToString()));
        if (!string.IsNullOrWhiteSpace(user.DisplayName))
            identity.AddClaim(new Claim(ClaimTypes.GivenName, user.DisplayName));

        if (user.MustChangePassword)
            identity.AddClaim(new Claim(PlatformClaimTypes.PasswordChangeRequired, "true"));

        if (!user.TwoFactorEnabled)
            identity.AddClaim(new Claim(PlatformClaimTypes.MfaEnrollmentRequired, "true"));

        return identity;
    }
}

/// <inheritdoc cref="IOperatorSessions"/>
internal sealed class OperatorSessions(IServiceScopeFactory scopes) : IOperatorSessions
{
    public async Task<OperatorSessionState?> FindAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (user.FindFirst(PlatformClaimTypes.OperatorId)?.Value is not { } id)
            return null;

        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(id) is not { Disabled: false } found)
            return null;

        // 安全戳記不符：密碼或兩步驟驗證被改過／重設，這張 cookie 簽發之後的事，舊的 session 一律失效。
        var stamp = user.FindFirst(users.Options.ClaimsIdentity.SecurityStampClaimType)?.Value;
        return string.Equals(stamp, found.SecurityStamp, StringComparison.Ordinal)
            ? new OperatorSessionState(found.MustChangePassword, found.TwoFactorEnabled)
            : null;
    }
}

/// <summary>
/// 「密碼已通過、等第二步」的暫存憑證（營運者專用，沒有租戶）：
/// 一張只存營運者 Id、五分鐘內有效的 cookie，<b>不是</b>後台的登入 cookie（不同 scheme，授權不認它）。
/// </summary>
internal sealed class OperatorMfaPendingCookie(IHttpContextAccessor http)
{
    public const string Scheme = "UiMfaPending";

    public static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(5);

    public Task IssueAsync(OperatorUser user)
    {
        var principal = new ClaimsPrincipal(new ClaimsIdentity([new Claim(PlatformClaimTypes.OperatorId, user.Id.ToString())], Scheme));
        return Context().SignInAsync(Scheme, principal, new AuthenticationProperties { IsPersistent = false });
    }

    /// <summary>暫存憑證裡的營運者 id；沒有或過期則 null。</summary>
    public async Task<string?> ReadAsync()
    {
        var result = await Context().AuthenticateAsync(Scheme);
        return result.Succeeded ? result.Principal.FindFirst(PlatformClaimTypes.OperatorId)?.Value : null;
    }

    public Task ClearAsync() => Context().SignOutAsync(Scheme);

    private HttpContext Context() => http.HttpContext ?? throw new InvalidOperationException("No HTTP request.");
}