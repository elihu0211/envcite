using System.Runtime.CompilerServices;
using System.Security.Claims;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members.Operators;

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
internal sealed class OperatorSessions(IServiceScopeFactory scopes, TimeProvider time) : IOperatorSessions
{
    /// <summary>預取的結果最多沿用多久。只夠 circuit 開啟到第一次渲染之間；超過就當沒預取，重查資料庫。</summary>
    internal static readonly TimeSpan PrefetchLife = TimeSpan.FromSeconds(5);

    private sealed record Prefetched(Task<OperatorSessionState?> Result, long StartedAt);

    // 以 principal 物件（不是它的 claim）為鍵：別的請求、別的 circuit 即使是同一位營運者，也拿不到這一份。
    private readonly ConditionalWeakTable<ClaimsPrincipal, Prefetched> _prefetched = [];

    public async Task PrefetchAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (user.FindFirst(PlatformClaimTypes.OperatorId) is null)
            return;

        var query = QueryAsync(user, cancellationToken);
        _prefetched.AddOrUpdate(user, new Prefetched(query, time.GetTimestamp()));
        try
        {
            await query;
        }
        catch (Exception exception)
        {
            // 預取只是搶先查：失敗就丟掉，授權時再查一次（資料庫真的有問題，那時照常失敗）。取消則往外傳，circuit 本來就在關。
            _prefetched.Remove(user);
            if (exception is OperationCanceledException)
                throw;
        }
    }

    public Task<OperatorSessionState?> FindAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        // 只用一次：取出就移除，之後的授權與重驗都回到每次查資料庫。
        if (_prefetched.TryGetValue(user, out var ready))
        {
            _prefetched.Remove(user);
            if (time.GetElapsedTime(ready.StartedAt) <= PrefetchLife && ready.Result is { IsFaulted: false, IsCanceled: false })
                return ready.Result;
        }

        return QueryAsync(user, cancellationToken);
    }

    private async Task<OperatorSessionState?> QueryAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
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