using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 客戶入口的帳密登入，與登入後改密碼。每個動作都在目前請求的租戶內，並留稽核。
/// 稽核寫入失敗會讓動作失敗（fail closed）：入口是政府機關也會用的，寧可登不進去也不要有一次沒留紀錄的登入。
/// </summary>
internal sealed class PortalAccount(
    UserManager<ApplicationUser> users,
    SignInManager<ApplicationUser> signIn,
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

        if (user.Disabled)
        {
            // 停用的人：仍然算一次密碼雜湊（別讓回應時間洩漏「這個帳號存在而且被停用」），訊息與密碼錯相同。
            _ = users.PasswordHasher.VerifyHashedPassword(user, user.PasswordHash ?? DummyHash.Value, password ?? string.Empty);
            await audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.PortalSignInFailed, info.Id, "member", user.Id, new Dictionary<string, object?> { ["reason"] = "disabled" }),
                cancellationToken);
            return PortalSignInStatus.Failed;
        }

        // Identity 原生：密碼對且啟用了兩步驟時，簽發它的待驗證 cookie 並不歸零失敗計數（要等第二步也過）。
        var result = await signIn.PasswordSignInAsync(user, password ?? string.Empty, isPersistent: false, lockoutOnFailure: true);
        if (result.RequiresTwoFactor)
        {
            return PortalSignInStatus.RequiresTwoFactor;
        }

        if (!result.Succeeded)
        {
            await audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.PortalSignInFailed, info.Id, "member", user.Id, new Dictionary<string, object?>
                {
                    ["reason"] = result.IsLockedOut ? "locked_out" : "bad_password",
                }),
                cancellationToken);
            return PortalSignInStatus.Failed;
        }

        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalSignedIn, info.Id, "member", user.Id), cancellationToken);
        return user.MustChangePassword ? PortalSignInStatus.MustChangePassword : PortalSignInStatus.Succeeded;
    }

    public async Task<ActivationState> InspectActivationAsync(string? userId, string? code, CancellationToken cancellationToken) =>
        await Activation.InspectAsync<ApplicationUser, string>(users, await FindAsync(userId), code);

    public async Task<AdminOutcome<ActivationState>> ActivateAsync(
        string? userId, string? code, string? newPassword, CancellationToken cancellationToken)
    {
        var info = tenant.MultiTenantContext?.TenantInfo ?? throw new InvalidOperationException("No tenant resolved for the portal request.");

        // 連結只對路徑上的租戶有效：租戶的查詢濾鏡讓別的租戶的人在這裡找不到。
        var (outcome, user) = await Activation.CompleteAsync<ApplicationUser, string>(users, await FindAsync(userId), code, newPassword);
        if (outcome.IsOk && user is not null)
        {
            await audit.AppendAsync(
                ActorOf(user),
                new AuditEntry(AuditActions.MemberActivated, info.Id, "member", user.Id, new Dictionary<string, object?>
                {
                    ["mode"] = outcome.Value == ActivationState.SetPassword ? "set_password" : "confirm_email",
                }),
                cancellationToken);
        }

        return outcome;
    }

    private async Task<ApplicationUser?> FindAsync(string? userId) =>
        string.IsNullOrWhiteSpace(userId) || userId.Length > 64 ? null : await users.FindByIdAsync(userId);

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

        // 換一張新的 cookie（安全戳記已隨改密碼更新，其他裝置的 session 會失效；這一張要接著用）。
        await signIn.RefreshSignInAsync(user);
        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalPasswordChanged, info.Id, "member", user.Id), cancellationToken);
        return AdminOutcome.Ok(true);
    }

    private static ClaimsPrincipal ActorOf(ApplicationUser user) =>
        new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, user.Id)], "portal"));

}