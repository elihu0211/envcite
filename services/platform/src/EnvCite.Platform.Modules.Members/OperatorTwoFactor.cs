using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 後台的兩步驟驗證（TOTP，RFC 6238）。結構與 <see cref="PortalTwoFactor"/> 相同，規則一樣，並且各有測試：
/// (1) 第二步的失敗與密碼失敗<b>共用一個</b>鎖定計數；(2) 只接受比上次成功更新的時間步（<see cref="TotpVerifier"/>）；
/// (3) 金鑰自己產生（<see cref="TotpSetup.NewKey"/>）；(4) 金鑰、驗證碼與復原碼不進稽核或日誌。
/// 差別：每位營運者都必須使用，所以沒有「關閉」；稽核在簽發 session 之前寫入（fail closed）。
/// </summary>
internal sealed class OperatorTwoFactor(
    UserManager<OperatorUser> users,
    OperatorAccount account,
    TotpVerifier totp,
    OperatorMfaPendingCookie pending,
    IAuditLog audit) : IOperatorTwoFactor
{
    private const int RecoveryCodeCount = 10;

    public async Task<bool> IsEnabledAsync(ClaimsPrincipal principal, CancellationToken cancellationToken) =>
        await UserOfAsync(principal) is { TwoFactorEnabled: true };

    public async Task<AdminOutcome<TwoFactorSetup>> BeginSetupAsync(ClaimsPrincipal principal, CancellationToken cancellationToken)
    {
        if (await UserOfAsync(principal) is not { } user)
        {
            return AdminOutcome.NotFound<TwoFactorSetup>(OutcomeCodes.AccountNotFound);
        }

        if (user.TwoFactorEnabled)
        {
            return AdminOutcome.Invalid<TwoFactorSetup>(OutcomeCodes.TwoFactorAlreadyEnabled);
        }

        // 沒有金鑰才產生；有就沿用，重新整理頁面不會讓已經掃進手機的 QR 失效。
        var key = await users.GetAuthenticatorKeyAsync(user);
        if (string.IsNullOrEmpty(key))
        {
            key = TotpSetup.NewKey();
            await users.SetAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey, key);
        }

        return AdminOutcome.Ok(TotpSetup.Create(user.Email, key));
    }

    public async Task<AdminOutcome<IReadOnlyList<string>>> EnableAsync(ClaimsPrincipal principal, string code, CancellationToken cancellationToken)
    {
        if (await UserOfAsync(principal) is not { } user)
        {
            return AdminOutcome.NotFound<IReadOnlyList<string>>(OutcomeCodes.AccountNotFound);
        }

        if (user.TwoFactorEnabled)
        {
            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.TwoFactorAlreadyEnabled);
        }

        if (!await totp.VerifyAsync(users, user, code))
        {
            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.VerificationCodeIncorrectHint);
        }

        await users.SetTwoFactorEnabledAsync(user, true);
        var codes = await users.GenerateNewTwoFactorRecoveryCodesAsync(user, RecoveryCodeCount) ?? [];

        // 啟用會換安全戳記（其他裝置的 session 失效）；這一個要換新的接著用。
        await users.UpdateSecurityStampAsync(user);
        await audit.AppendAsync(OperatorAccount.ActorOf(user), new AuditEntry(AuditActions.OperatorMfaEnabled, TargetType: "operator", TargetId: user.Id.ToString()), cancellationToken);
        await account.IssueSessionAsync(user);
        return AdminOutcome.Ok<IReadOnlyList<string>>([.. codes]);
    }

    public async Task<AdminOutcome<IReadOnlyList<string>>> RegenerateRecoveryCodesAsync(
        ClaimsPrincipal principal, string code, CancellationToken cancellationToken)
    {
        if (await UserOfAsync(principal) is not { TwoFactorEnabled: true } user)
        {
            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.TwoFactorNotEnabled);
        }

        // 驗證碼錯記一次失敗並檢查鎖定，鎖定中就一律不過；這裡不歸零計數（歸零只在完整登入成功時）。
        if (await users.IsLockedOutAsync(user) || !await totp.VerifyAsync(users, user, code))
        {
            if (!await users.IsLockedOutAsync(user))
            {
                await users.AccessFailedAsync(user);
            }

            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.VerificationCodeIncorrect);
        }

        var codes = await users.GenerateNewTwoFactorRecoveryCodesAsync(user, RecoveryCodeCount) ?? [];
        await audit.AppendAsync(
            OperatorAccount.ActorOf(user),
            new AuditEntry(AuditActions.OperatorRecoveryCodesRegenerated, TargetType: "operator", TargetId: user.Id.ToString()),
            cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<string>>([.. codes]);
    }

    public async Task<bool> HasPendingSignInAsync() => await pending.ReadAsync() is not null;

    public async Task<SignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken)
    {
        if (await pending.ReadAsync() is not { } id || await users.FindOperatorAsync(id) is not { } user)
        {
            return SignInStatus.Failed;
        }

        // 在第一步之後、第二步之前被停用或被重設了兩步驟驗證：不再給任何機會，暫存憑證收回。
        if (user.Disabled || !user.TwoFactorEnabled)
        {
            await pending.ClearAsync();
            return SignInStatus.Failed;
        }

        // 第二步的失敗與密碼失敗共用鎖定計數；已鎖定就不再給猜的機會，並丟掉暫存憑證，要從密碼重來。
        if (await users.IsLockedOutAsync(user))
        {
            await pending.ClearAsync();
            return SignInStatus.Failed;
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
                new AuditEntry(AuditActions.OperatorSignInDenied, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?>
                {
                    ["email"] = user.Email,
                    ["reason"] = lockedNow ? "locked_out" : isRecoveryCode ? "bad_recovery_code" : "bad_totp",
                }),
                cancellationToken);
            return SignInStatus.Failed;
        }

        await users.ResetAccessFailedCountAsync(user);
        await pending.ClearAsync();

        // 稽核裡只記用了哪一種方式，絕不記碼本身；先寫稽核，再簽發 session。
        await audit.AppendAsync(
            OperatorAccount.ActorOf(user),
            new AuditEntry(AuditActions.OperatorSignedIn, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["method"] = isRecoveryCode ? "recovery_code" : "totp" }),
            cancellationToken);
        if (isRecoveryCode)
        {
            await audit.AppendAsync(OperatorAccount.ActorOf(user), new AuditEntry(AuditActions.OperatorRecoveryCodeUsed, TargetType: "operator", TargetId: user.Id.ToString()), cancellationToken);
        }

        await account.IssueSessionAsync(user);
        return user.MustChangePassword ? SignInStatus.MustChangePassword : SignInStatus.Succeeded;
    }

    private async Task<OperatorUser?> UserOfAsync(ClaimsPrincipal principal) =>
        await users.FindOperatorAsync(principal.FindFirst(PlatformClaimTypes.OperatorId)?.Value);
}