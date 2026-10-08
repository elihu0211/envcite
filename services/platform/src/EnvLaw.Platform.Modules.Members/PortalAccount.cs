using System.Security.Claims;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

namespace EnvLaw.Platform.Modules.Members;

/// <summary>
/// 客戶入口的帳密登入，與登入後改密碼。每個動作都在目前請求的租戶內，並留稽核。
/// 稽核寫入失敗會讓動作失敗（fail closed）：入口是政府機關也會用的，寧可登不進去也不要有一次沒留紀錄的登入。
/// </summary>
internal sealed class PortalAccount(
    UserManager<ApplicationUser> users,
    SignInManager<ApplicationUser> signIn,
    MfaPendingCookie pending,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IAuditLog audit) : IPortalAccount
{
    // 不存在的帳號也要花同樣的時間算一次雜湊：否則回應快慢就在告訴攻擊者哪些 email 有帳號。
    private static readonly Lazy<string> DummyHash = new(() => new PasswordHasher<ApplicationUser>().HashPassword(new ApplicationUser(), "dummy-password-for-timing"));

    public PortalTenant? CurrentTenant =>
        tenant.MultiTenantContext?.TenantInfo is { Id: { } id, Identifier: { } identifier } info ? new PortalTenant(id, identifier, info.Name) : null;

    public async Task<PortalSignInStatus> SignInAsync(string email, string password, CancellationToken cancellationToken)
    {
        var info = tenant.MultiTenantContext?.TenantInfo ?? throw new InvalidOperationException("No tenant resolved for the portal request.");

        var user = string.IsNullOrWhiteSpace(email) ? null : await users.FindByEmailAsync(email.Trim());
        if (user is null)
        {
            _ = users.PasswordHasher.VerifyHashedPassword(new ApplicationUser(), DummyHash.Value, password ?? string.Empty);
            // 不記錄使用者打的 email：人常把密碼貼到帳號欄，稽核表又是只能附加、刪不掉的。
            await audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.PortalSignInFailed, info.Id, Detail: new Dictionary<string, object?> { ["reason"] = "unknown_user" }),
                cancellationToken);
            return PortalSignInStatus.Failed;
        }

        // 自己做鎖定的記帳，不用 PasswordSignInAsync：它在密碼對的時候把失敗計數歸零，
        // 而有兩步驟驗證的帳號，密碼對之後還要過第二步——歸零了，知道密碼的人就能每輪重新輸入密碼，
        // 無限次猜六位數。所以這裡密碼對不歸零，要等第二步也過了才歸零。
        if (await users.IsLockedOutAsync(user) || !await users.CheckPasswordAsync(user, password ?? string.Empty))
        {
            var locked = await users.IsLockedOutAsync(user);
            if (!locked)
            {
                await users.AccessFailedAsync(user);
                locked = await users.IsLockedOutAsync(user);
            }

            await audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.PortalSignInFailed, info.Id, "member", user.Id, new Dictionary<string, object?>
                {
                    ["reason"] = locked ? "locked_out" : "bad_password",
                }),
                cancellationToken);
            return PortalSignInStatus.Failed;
        }

        if (user.TwoFactorEnabled)
        {
            // 還不簽發登入 cookie：只給一張只能用來輸入第二步驗證碼的暫存憑證。
            await pending.IssueAsync(user);
            return PortalSignInStatus.RequiresTwoFactor;
        }

        await users.ResetAccessFailedCountAsync(user);
        await signIn.SignInAsync(user, isPersistent: false);
        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalSignedIn, info.Id, "member", user.Id), cancellationToken);
        return user.MustChangePassword ? PortalSignInStatus.MustChangePassword : PortalSignInStatus.Succeeded;
    }

    public async Task SignOutAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        await signIn.SignOutAsync();
        if (user.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is { } id && tenant.MultiTenantContext?.TenantInfo is { } info)
        {
            await audit.AppendAsync(user, new AuditEntry(AuditActions.PortalSignedOut, info.Id, "member", id), cancellationToken);
        }
    }

    public async Task<AdminOutcome<bool>> ChangePasswordAsync(
        ClaimsPrincipal principal, string currentPassword, string newPassword, CancellationToken cancellationToken)
    {
        var info = tenant.MultiTenantContext?.TenantInfo ?? throw new InvalidOperationException("No tenant resolved for the portal request.");
        var id = principal.FindFirst(PlatformClaimTypes.PortalUserId)?.Value;
        var user = id is null ? null : await users.FindByIdAsync(id);
        if (user is null)
        {
            return AdminOutcome.NotFound<bool>();
        }

        if (string.Equals(currentPassword, newPassword, StringComparison.Ordinal))
        {
            return AdminOutcome.Invalid<bool>("新密碼不能與目前的密碼相同。");
        }

        var changed = await users.ChangePasswordAsync(user, currentPassword ?? string.Empty, newPassword ?? string.Empty);
        if (!changed.Succeeded)
        {
            return AdminOutcome.Invalid<bool>(Describe(changed.Errors.First()));
        }

        user.MustChangePassword = false;
        await users.UpdateAsync(user);

        // 換一張新的 cookie（安全戳記已隨改密碼更新，其他裝置的 session 會失效；這一張要接著用）。
        await signIn.RefreshSignInAsync(user);
        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalPasswordChanged, info.Id, "member", user.Id), cancellationToken);
        return AdminOutcome.Ok(true);
    }

    private static ClaimsPrincipal ActorOf(ApplicationUser user) =>
        new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, user.Id)], "portal"));

    private static string Describe(IdentityError error) => error.Code switch
    {
        nameof(IdentityErrorDescriber.PasswordMismatch) => "目前的密碼不正確。",
        nameof(IdentityErrorDescriber.PasswordTooShort) => $"新密碼至少要 {MembersModule.MinimumPasswordLength} 個字元。",
        _ => "新密碼不符合規則。",
    };
}