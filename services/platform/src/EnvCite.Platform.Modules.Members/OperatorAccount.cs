using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 後台的帳密登入、登出與改密碼。結構與 <see cref="PortalAccount"/> 相同，差別在：沒有租戶；每位營運者都必須使用
/// 兩步驟驗證；稽核事件在簽發 session <b>之前</b>寫入——寫入失敗就沒有 session（fail closed）。
/// 鎖定記帳也與入口相同：密碼對<b>不</b>歸零失敗計數，要等第二步也過了才歸零，否則知道密碼的人能每輪重新輸入密碼，
/// 無限次猜六位數。
/// </summary>
internal sealed class OperatorAccount(
    UserManager<OperatorUser> users,
    IUserClaimsPrincipalFactory<OperatorUser> claims,
    OperatorMfaPendingCookie pending,
    IHttpContextAccessor http,
    IAuditLog audit) : IOperatorAccount
{
    // 不存在的帳號也要花同樣的時間算一次雜湊：否則回應快慢就在告訴攻擊者哪些 email 有帳號。
    private static readonly Lazy<string> DummyHash = new(() => new PasswordHasher<OperatorUser>().HashPassword(new OperatorUser(), "dummy-password-for-timing"));

    public async Task<OperatorSignInStatus> SignInAsync(string email, string password, CancellationToken cancellationToken)
    {
        var user = string.IsNullOrWhiteSpace(email) ? null : await users.FindByEmailAsync(email.Trim());
        if (user is null)
        {
            _ = users.PasswordHasher.VerifyHashedPassword(new OperatorUser(), DummyHash.Value, password ?? string.Empty);
            await DeniedAsync(email, null, "unknown_user", cancellationToken);
            return OperatorSignInStatus.Failed;
        }

        if (user.Disabled)
        {
            // 停用的人：仍然算一次密碼雜湊（別讓回應時間洩漏「這個帳號存在而且被停用」），訊息與密碼錯相同。
            _ = users.PasswordHasher.VerifyHashedPassword(user, user.PasswordHash ?? DummyHash.Value, password ?? string.Empty);
            await DeniedAsync(email, user, "disabled", cancellationToken);
            return OperatorSignInStatus.Failed;
        }

        if (await users.IsLockedOutAsync(user) || !await users.CheckPasswordAsync(user, password ?? string.Empty))
        {
            var locked = await users.IsLockedOutAsync(user);
            if (!locked)
            {
                await users.AccessFailedAsync(user);
                locked = await users.IsLockedOutAsync(user);
            }

            await DeniedAsync(email, user, locked ? "locked_out" : "bad_password", cancellationToken);
            return OperatorSignInStatus.Failed;
        }

        if (user.TwoFactorEnabled)
        {
            // 還不簽發 session：只給一張只能用來輸入第二步驗證碼的暫存憑證。
            await pending.IssueAsync(user);
            return OperatorSignInStatus.RequiresTwoFactor;
        }

        // 沒有兩步驟驗證：只可能是還沒註冊（每位營運者都必須使用）。給一張受限的 session，只能進改密碼頁或註冊頁。
        await users.ResetAccessFailedCountAsync(user);
        await audit.AppendAsync(
            ActorOf(user),
            new AuditEntry(AuditActions.OperatorSignedIn, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["method"] = "password" }),
            cancellationToken);
        await IssueSessionAsync(user);
        return user.MustChangePassword ? OperatorSignInStatus.MustChangePassword : OperatorSignInStatus.MustEnrollTwoFactor;
    }

    public async Task SignOutAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        if (user.FindFirst(PlatformClaimTypes.OperatorId)?.Value is { } id)
        {
            await audit.AppendAsync(user, new AuditEntry(AuditActions.OperatorSignedOut, TargetType: "operator", TargetId: id), cancellationToken);
        }

        await pending.ClearAsync();
        await Context().SignOutAsync(PlatformAuth.UiCookieScheme);
    }

    public async Task<AdminOutcome<bool>> ChangePasswordAsync(
        ClaimsPrincipal principal, string currentPassword, string newPassword, CancellationToken cancellationToken)
    {
        var user = await users.FindOperatorAsync(principal.FindFirst(PlatformClaimTypes.OperatorId)?.Value);
        if (user is null)
        {
            return AdminOutcome.NotFound<bool>(OutcomeCodes.AccountNotFound);
        }

        if (string.Equals(currentPassword, newPassword, StringComparison.Ordinal))
        {
            return AdminOutcome.Invalid<bool>(OutcomeCodes.PasswordSameAsCurrent);
        }

        var changed = await users.ChangePasswordAsync(user, currentPassword ?? string.Empty, newPassword ?? string.Empty);
        if (!changed.Succeeded)
        {
            return IdentityOutcomes.PasswordRejected<bool>(changed.Errors.First());
        }

        user.MustChangePassword = false;
        await users.UpdateAsync(user);

        // 安全戳記已隨改密碼更新，其他裝置的 session 失效；這一張換新的接著用。
        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.OperatorPasswordChanged, TargetType: "operator", TargetId: user.Id.ToString()), cancellationToken);
        await IssueSessionAsync(user);
        return AdminOutcome.Ok(true);
    }

    /// <summary>簽發後台的登入 cookie。呼叫端要先寫稽核：寫不進去就不該有 session。</summary>
    internal async Task IssueSessionAsync(OperatorUser user) =>
        await Context().SignInAsync(PlatformAuth.UiCookieScheme, await claims.CreateAsync(user), new AuthenticationProperties { IsPersistent = false });

    /// <summary>
    /// 被拒絕的嘗試也留稽核（fail closed：寫不進去，這次登入就失敗），並記下嘗試登入的 email。
    /// 人常把密碼貼到帳號欄，而稽核表是只能附加、刪不掉的，所以只有「長得像 email」的才記。
    /// </summary>
    private async Task DeniedAsync(string? attemptedEmail, OperatorUser? user, string reason, CancellationToken cancellationToken) =>
        await audit.AppendAsync(
            new ClaimsPrincipal(),
            new AuditEntry(
                AuditActions.OperatorSignInDenied,
                TargetType: user is null ? null : "operator",
                TargetId: user?.Id.ToString(),
                Detail: new Dictionary<string, object?>
                {
                    ["email"] = EmailAddresses.IsValid(attemptedEmail) ? attemptedEmail!.Trim() : null,
                    ["reason"] = reason,
                }),
            cancellationToken);

    internal static ClaimsPrincipal ActorOf(OperatorUser user) =>
        new(new ClaimsIdentity(
            [new Claim(PlatformClaimTypes.OperatorId, user.Id.ToString()), new Claim(ClaimTypes.Email, user.Email ?? string.Empty)],
            "operator"));

    private HttpContext Context() => http.HttpContext ?? throw new InvalidOperationException("No HTTP request.");

}