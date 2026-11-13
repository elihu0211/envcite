using System.Security.Claims;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 營運者（<see cref="OperatorTwoFactor"/>）與入口成員（<see cref="PortalTwoFactor"/>）的兩步驟驗證共有的骨架：
/// 註冊、啟用、重新產生復原碼。兩邊守同一組規則，並且各有測試：
/// (1) 第二步的失敗與密碼失敗<b>共用一個</b>鎖定計數，敏感動作前的再確認也一樣；
/// (2) 金鑰自己產生並存放，不用 <c>ResetAuthenticatorKeyAsync</c>（它換安全戳記，設定頁在渲染中執行，無法再刷新 cookie）；
/// (3) 金鑰、驗證碼與復原碼不進稽核或日誌。
/// 兩邊不同的只有：怎麼驗證一組碼、啟用後怎麼換新 session、稽核寫什麼，以及登入的第二步（機制不同，留在各自的類別）。
/// </summary>
internal abstract class TwoFactorBase<TUser, TKey>(UserManager<TUser> users, IAuditLog audit) : ITwoFactor
    where TUser : IdentityUser<TKey>
    where TKey : IEquatable<TKey>
{
    private const int RecoveryCodeCount = 10;

    protected UserManager<TUser> Users { get; } = users;

    protected IAuditLog Audit { get; } = audit;

    protected abstract Task<TUser?> UserOfAsync(ClaimsPrincipal principal);

    /// <summary>驗證一組驗證器的碼。失敗不計次數：計次與鎖定由呼叫它的流程負責。</summary>
    protected abstract Task<bool> VerifyCodeAsync(TUser user, string? code);

    /// <summary>啟用之後（安全戳記已換）：寫稽核，並讓目前這個 session 接著能用。</summary>
    protected abstract Task OnEnabledAsync(TUser user, CancellationToken cancellationToken);

    protected abstract Task OnRecoveryCodesRegeneratedAsync(TUser user, CancellationToken cancellationToken);

    public abstract Task<bool> HasPendingSignInAsync();

    public abstract Task<SignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken);

    public async Task<bool> IsEnabledAsync(ClaimsPrincipal principal, CancellationToken cancellationToken) =>
        await UserOfAsync(principal) is { TwoFactorEnabled: true };

    public async Task<AdminOutcome<TwoFactorSetup>> BeginSetupAsync(ClaimsPrincipal principal, CancellationToken cancellationToken)
    {
        if (await UserOfAsync(principal) is not { } user)
            return AdminOutcome.NotFound<TwoFactorSetup>(OutcomeCodes.AccountNotFound);

        if (user.TwoFactorEnabled)
            return AdminOutcome.Invalid<TwoFactorSetup>(OutcomeCodes.TwoFactorAlreadyEnabled);

        // 沒有金鑰才產生；有就沿用，重新整理頁面不會讓已經掃進手機的 QR 失效。
        var key = await Users.GetAuthenticatorKeyAsync(user);
        if (string.IsNullOrEmpty(key))
        {
            key = Users.GenerateNewAuthenticatorKey();
            await Users.SetAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey, key);
        }

        return AdminOutcome.Ok(TotpSetup.Create(user.Email, key!));
    }

    public async Task<AdminOutcome<IReadOnlyList<string>>> EnableAsync(ClaimsPrincipal principal, string code, CancellationToken cancellationToken)
    {
        if (await UserOfAsync(principal) is not { } user)
            return AdminOutcome.NotFound<IReadOnlyList<string>>(OutcomeCodes.AccountNotFound);

        if (user.TwoFactorEnabled)
            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.TwoFactorAlreadyEnabled);

        if (!await VerifyCodeAsync(user, code))
            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.VerificationCodeIncorrectHint);

        await Users.SetTwoFactorEnabledAsync(user, true);
        var codes = await NewRecoveryCodesAsync(user);

        // 啟用會換安全戳記（其他裝置的 session 失效）；這一個要接著用。
        await Users.UpdateSecurityStampAsync(user);
        await OnEnabledAsync(user, cancellationToken);
        return AdminOutcome.Ok(codes);
    }

    public async Task<AdminOutcome<IReadOnlyList<string>>> RegenerateRecoveryCodesAsync(
        ClaimsPrincipal principal, string code, CancellationToken cancellationToken)
    {
        if (await UserOfAsync(principal) is not { TwoFactorEnabled: true } user)
            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.TwoFactorNotEnabled);

        if (!await ConfirmAsync(user, code))
            return AdminOutcome.Invalid<IReadOnlyList<string>>(OutcomeCodes.VerificationCodeIncorrect);

        var codes = await NewRecoveryCodesAsync(user);
        await OnRecoveryCodesRegeneratedAsync(user, cancellationToken);
        return AdminOutcome.Ok(codes);
    }

    /// <summary>
    /// 敏感動作前再確認一次本人：驗證碼（與密碼，若有給）。任何一項錯都記一次失敗並檢查鎖定，
    /// 鎖定中就一律不過。這裡不歸零計數：歸零只在完整登入成功時。
    /// </summary>
    protected async Task<bool> ConfirmAsync(TUser user, string? code, string? password = null)
    {
        if (await Users.IsLockedOutAsync(user))
            return false;

        var ok = (password is null || await Users.CheckPasswordAsync(user, password)) && await VerifyCodeAsync(user, code);
        if (!ok)
            await Users.AccessFailedAsync(user);

        return ok;
    }

    private async Task<IReadOnlyList<string>> NewRecoveryCodesAsync(TUser user) =>
        [.. await Users.GenerateNewTwoFactorRecoveryCodesAsync(user, RecoveryCodeCount) ?? []];
}