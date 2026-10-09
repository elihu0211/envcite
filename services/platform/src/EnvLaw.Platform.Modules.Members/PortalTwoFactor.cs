using System.Security.Claims;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.Members;

/// <inheritdoc cref="IPortalTwoFactor"/>
internal sealed class PortalTwoFactor(
    UserManager<ApplicationUser> users,
    SignInManager<ApplicationUser> signIn,
    TotpVerifier totp,
    MfaPendingCookie pending,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IOptions<MembersOptions> options,
    IAuditLog audit) : IPortalTwoFactor
{
    private const int RecoveryCodeCount = 10;

    /// <summary>Owner 與 Admin 被要求使用兩步驟驗證（設定可關閉，預設要求）。</summary>
    internal static bool RequiresTwoFactor(string role, MembersOptions options) =>
        options.RequireMfaForAdmins && role is CustomerRoles.Owner or CustomerRoles.Admin;

    public bool IsRequired(ClaimsPrincipal principal) =>
        principal.FindFirst(ClaimTypes.Role)?.Value is { } role && RequiresTwoFactor(role, options.Value);

    public async Task<bool> IsEnabledAsync(ClaimsPrincipal principal, CancellationToken cancellationToken) =>
        await UserOfAsync(principal) is { TwoFactorEnabled: true };

    public async Task<AdminOutcome<TwoFactorSetup>> BeginSetupAsync(ClaimsPrincipal principal, CancellationToken cancellationToken)
    {
        if (await UserOfAsync(principal) is not { } user)
        {
            return AdminOutcome.NotFound<TwoFactorSetup>();
        }

        if (user.TwoFactorEnabled)
        {
            return AdminOutcome.Invalid<TwoFactorSetup>("已經啟用了兩步驟驗證。");
        }

        // 沒有金鑰才產生；有就沿用，重新整理頁面不會讓已經掃進手機的 QR 失效。
        var key = await users.GetAuthenticatorKeyAsync(user);
        if (string.IsNullOrEmpty(key))
        {
            // 自己產生並直接寫入（理由見 TotpSetup.NewKey：ResetAuthenticatorKeyAsync 會讓自己目前的 session 失效，測試抓到的）。
            key = TotpSetup.NewKey();
            await users.SetAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey, key);
        }

        return AdminOutcome.Ok(TotpSetup.Create(user.Email, key!));
    }

    public async Task<AdminOutcome<IReadOnlyList<string>>> EnableAsync(ClaimsPrincipal principal, string code, CancellationToken cancellationToken)
    {
        var info = Tenant();
        if (await UserOfAsync(principal) is not { } user)
        {
            return AdminOutcome.NotFound<IReadOnlyList<string>>();
        }

        if (user.TwoFactorEnabled)
        {
            return AdminOutcome.Invalid<IReadOnlyList<string>>("已經啟用了兩步驟驗證。");
        }

        if (!await totp.VerifyAsync(users, user, code))
        {
            return AdminOutcome.Invalid<IReadOnlyList<string>>("驗證碼不正確。請確認手機的時間是自動校正的，再輸入驗證器上現在顯示的六位數。");
        }

        await users.SetTwoFactorEnabledAsync(user, true);
        var codes = await users.GenerateNewTwoFactorRecoveryCodesAsync(user, RecoveryCodeCount) ?? [];

        // 啟用會換安全戳記（其他裝置的 session 失效）；這一個要接著用。
        await users.UpdateSecurityStampAsync(user);
        await signIn.RefreshSignInAsync(user);
        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalMfaEnabled, info.Id, "member", user.Id), cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<string>>([.. codes]);
    }

    public async Task<AdminOutcome<bool>> DisableAsync(ClaimsPrincipal principal, string password, string code, CancellationToken cancellationToken)
    {
        var info = Tenant();
        if (await UserOfAsync(principal) is not { } user)
        {
            return AdminOutcome.NotFound<bool>();
        }

        if (!user.TwoFactorEnabled)
        {
            return AdminOutcome.Invalid<bool>("目前沒有啟用兩步驟驗證。");
        }

        // 角色要求的就不能關：能關，等於這個要求只是個建議。要關得由營運者改角色或設定。
        if (RequiresTwoFactor(user.Role, options.Value))
        {
            return AdminOutcome.Invalid<bool>("你的角色必須使用兩步驟驗證，不能關閉。");
        }

        // 密碼與驗證碼都要對：只知道其中一個（看到密碼、或借到沒鎖的手機）的人，不能把保護關掉。
        // 兩者的失敗都計入鎖定，所以這裡不能成為猜測的後門。
        if (!await ConfirmAsync(user, password, code))
        {
            return AdminOutcome.Invalid<bool>("密碼或驗證碼不正確。");
        }

        await users.SetTwoFactorEnabledAsync(user, false);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.RecoveryCodes);
        await users.UpdateSecurityStampAsync(user);
        await signIn.RefreshSignInAsync(user);
        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalMfaDisabled, info.Id, "member", user.Id), cancellationToken);
        return AdminOutcome.Ok(true);
    }

    public async Task<AdminOutcome<IReadOnlyList<string>>> RegenerateRecoveryCodesAsync(
        ClaimsPrincipal principal, string code, CancellationToken cancellationToken)
    {
        var info = Tenant();
        if (await UserOfAsync(principal) is not { TwoFactorEnabled: true } user)
        {
            return AdminOutcome.Invalid<IReadOnlyList<string>>("目前沒有啟用兩步驟驗證。");
        }

        if (!await ConfirmAsync(user, password: null, code))
        {
            return AdminOutcome.Invalid<IReadOnlyList<string>>("驗證碼不正確。");
        }

        var codes = await users.GenerateNewTwoFactorRecoveryCodesAsync(user, RecoveryCodeCount) ?? [];
        await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalRecoveryCodesRegenerated, info.Id, "member", user.Id), cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<string>>([.. codes]);
    }

    /// <summary>
    /// 敏感動作前再確認一次本人：驗證碼（與密碼，若有給）。任何一項錯都記一次失敗並檢查鎖定，
    /// 鎖定中就一律不過。這裡不歸零計數：歸零只在完整登入成功時。
    /// </summary>
    private async Task<bool> ConfirmAsync(ApplicationUser user, string? password, string code)
    {
        if (await users.IsLockedOutAsync(user))
        {
            return false;
        }

        var ok = (password is null || await users.CheckPasswordAsync(user, password)) && await totp.VerifyAsync(users, user, code);
        if (!ok)
        {
            await users.AccessFailedAsync(user);
        }

        return ok;
    }

    public async Task<bool> HasPendingSignInAsync() => await pending.ReadAsync() is not null;

    public async Task<PortalSignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken)
    {
        var info = Tenant();
        if (await pending.ReadAsync() is not { } id || await users.FindByIdAsync(id) is not { } user)
        {
            return PortalSignInStatus.Failed;
        }

        // 在第一步之後、第二步之前被停用：不再給任何機會，暫存憑證收回。
        if (user.Disabled)
        {
            await pending.ClearAsync();
            return PortalSignInStatus.Failed;
        }

        // 第二步的失敗與密碼失敗共用鎖定計數；已鎖定就不再給猜的機會，並丟掉暫存憑證，要從密碼重來。
        if (await users.IsLockedOutAsync(user))
        {
            await pending.ClearAsync();
            return PortalSignInStatus.Failed;
        }

        var ok = isRecoveryCode
            ? (await users.RedeemTwoFactorRecoveryCodeAsync(user, (code ?? string.Empty).Trim())).Succeeded
            : await totp.VerifyAsync(users, user, code);
        if (!ok)
        {
            await users.AccessFailedAsync(user);
            var lockedNow = await users.IsLockedOutAsync(user);
            if (lockedNow)
            {
                await pending.ClearAsync();
            }

            await audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.PortalSignInFailed, info.Id, "member", user.Id, new Dictionary<string, object?>
                {
                    ["reason"] = lockedNow ? "locked_out" : isRecoveryCode ? "bad_recovery_code" : "bad_totp",
                }),
                cancellationToken);
            return PortalSignInStatus.Failed;
        }

        await users.ResetAccessFailedCountAsync(user);
        await pending.ClearAsync();
        await signIn.SignInAsync(user, isPersistent: false);

        // 稽核裡只記用了哪一種方式，絕不記碼本身。
        await audit.AppendAsync(
            ActorOf(user),
            new AuditEntry(AuditActions.PortalSignedIn, info.Id, "member", user.Id, new Dictionary<string, object?> { ["method"] = isRecoveryCode ? "recovery_code" : "totp" }),
            cancellationToken);
        if (isRecoveryCode)
        {
            await audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalRecoveryCodeUsed, info.Id, "member", user.Id), cancellationToken);
        }

        return user.MustChangePassword ? PortalSignInStatus.MustChangePassword : PortalSignInStatus.Succeeded;
    }

    private async Task<ApplicationUser?> UserOfAsync(ClaimsPrincipal principal) =>
        principal.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is { } id ? await users.FindByIdAsync(id) : null;

    private TenantInfo Tenant() =>
        tenant.MultiTenantContext?.TenantInfo ?? throw new InvalidOperationException("No tenant resolved for the portal request.");

    private static ClaimsPrincipal ActorOf(ApplicationUser user) =>
        new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, user.Id)], "portal"));
}