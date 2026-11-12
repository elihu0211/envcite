using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 後台的兩步驟驗證（TOTP，RFC 6238）；共有的規則與骨架在 <see cref="TwoFactorBase{TUser,TKey}"/>。
/// 差別：每位營運者都必須使用，所以沒有「關閉」；只接受比上次成功更新的時間步（<see cref="TotpVerifier"/>）；
/// 稽核在簽發 session 之前寫入（fail closed）。
/// </summary>
internal sealed class OperatorTwoFactor(
    UserManager<OperatorUser> users,
    OperatorAccount account,
    TotpVerifier totp,
    OperatorMfaPendingCookie pending,
    IAuditLog audit) : TwoFactorBase<OperatorUser, Guid>(users, audit), IOperatorTwoFactor
{
    protected override async Task<OperatorUser?> UserOfAsync(ClaimsPrincipal principal) =>
        await Users.FindOperatorAsync(principal.FindFirst(PlatformClaimTypes.OperatorId)?.Value);

    protected override Task<bool> VerifyCodeAsync(OperatorUser user, string? code) => totp.VerifyAsync(Users, user, code);

    protected override async Task OnEnabledAsync(OperatorUser user, CancellationToken cancellationToken)
    {
        await Audit.AppendAsync(OperatorAccount.ActorOf(user), new AuditEntry(AuditActions.OperatorMfaEnabled, TargetType: "operator", TargetId: user.Id.ToString()), cancellationToken);
        await account.IssueSessionAsync(user);
    }

    protected override Task OnRecoveryCodesRegeneratedAsync(OperatorUser user, CancellationToken cancellationToken) =>
        Audit.AppendAsync(
            OperatorAccount.ActorOf(user),
            new AuditEntry(AuditActions.OperatorRecoveryCodesRegenerated, TargetType: "operator", TargetId: user.Id.ToString()),
            cancellationToken);

    public override async Task<bool> HasPendingSignInAsync() => await pending.ReadAsync() is not null;

    public override async Task<SignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken)
    {
        if (await pending.ReadAsync() is not { } id || await Users.FindOperatorAsync(id) is not { } user)
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
        if (await Users.IsLockedOutAsync(user))
        {
            await pending.ClearAsync();
            return SignInStatus.Failed;
        }

        var ok = isRecoveryCode
            ? (await Users.RedeemTwoFactorRecoveryCodeAsync(user, (code ?? string.Empty).Trim())).Succeeded
            : await totp.VerifyAsync(Users, user, code);
        if (!ok)
        {
            await Users.AccessFailedAsync(user);
            var lockedNow = await Users.IsLockedOutAsync(user);
            if (lockedNow)
            {
                await pending.ClearAsync();
            }

            await Audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.OperatorSignInDenied, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?>
                {
                    ["email"] = user.Email,
                    ["reason"] = lockedNow ? "locked_out" : isRecoveryCode ? "bad_recovery_code" : "bad_totp",
                }),
                cancellationToken);
            return SignInStatus.Failed;
        }

        await Users.ResetAccessFailedCountAsync(user);
        await pending.ClearAsync();

        // 稽核裡只記用了哪一種方式，絕不記碼本身；先寫稽核，再簽發 session。
        await Audit.AppendAsync(
            OperatorAccount.ActorOf(user),
            new AuditEntry(AuditActions.OperatorSignedIn, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["method"] = isRecoveryCode ? "recovery_code" : "totp" }),
            cancellationToken);
        if (isRecoveryCode)
        {
            await Audit.AppendAsync(OperatorAccount.ActorOf(user), new AuditEntry(AuditActions.OperatorRecoveryCodeUsed, TargetType: "operator", TargetId: user.Id.ToString()), cancellationToken);
        }

        await account.IssueSessionAsync(user);
        return user.MustChangePassword ? SignInStatus.MustChangePassword : SignInStatus.Succeeded;
    }
}