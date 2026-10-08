using System.Security.Claims;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

using OtpNet;

namespace EnvLaw.Platform.Modules.Members;

/// <inheritdoc cref="IPortalTwoFactor"/>
internal sealed class PortalTwoFactor(
    UserManager<ApplicationUser> users,
    SignInManager<ApplicationUser> signIn,
    TotpVerifier totp,
    MfaPendingCookie pending,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IAuditLog audit) : IPortalTwoFactor
{
    private const int RecoveryCodeCount = 10;
    private const string Issuer = "env-law";

    // Identity 存驗證器金鑰的固定位置（與 GetAuthenticatorKeyAsync 讀的是同一格）。
    private const string AuthenticatorKeyProvider = "[AspNetUserStore]";
    private const string AuthenticatorKeyName = "AuthenticatorKey";

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
            // 自己產生並直接寫入，不用 ResetAuthenticatorKeyAsync：它會順手更新安全戳記，
            // 使用者一打開設定頁，自己目前的 session 就失效了（測試抓到的）。20 位元組 = RFC 4226 建議的長度。
            key = Base32Encoding.ToString(KeyGeneration.GenerateRandomKey(20));
            await users.SetAuthenticationTokenAsync(user, AuthenticatorKeyProvider, AuthenticatorKeyName, key);
        }

        var label = Uri.EscapeDataString($"{Issuer}:{user.Email}");
        var uri = $"otpauth://totp/{label}?secret={key}&issuer={Uri.EscapeDataString(Issuer)}&digits=6&period=30";
        return AdminOutcome.Ok(new TwoFactorSetup(Grouped(key!), uri));
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

        if (!await totp.VerifyAsync(user, code))
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

    public async Task<bool> HasPendingSignInAsync() => await pending.ReadAsync() is not null;

    public async Task<PortalSignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken)
    {
        var info = Tenant();
        if (await pending.ReadAsync() is not { } id || await users.FindByIdAsync(id) is not { } user)
        {
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
            : await totp.VerifyAsync(user, code);
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

    /// <summary>四個字元一組，方便手動輸入時核對。</summary>
    private static string Grouped(string key) =>
        string.Join(' ', Enumerable.Range(0, (key.Length + 3) / 4).Select(i => key.Substring(i * 4, Math.Min(4, key.Length - (i * 4)))));
}