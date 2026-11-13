using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <inheritdoc cref="IPortalTwoFactor"/>
internal sealed class PortalTwoFactor(
    UserManager<ApplicationUser> users,
    SignInManager<ApplicationUser> signIn,
    IHttpContextAccessor http,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IOptions<MembersOptions> options,
    IAuditLog audit) : TwoFactorBase<ApplicationUser, string>(users, audit), IPortalTwoFactor
{
    /// <summary>Owner 與 Admin 被要求使用兩步驟驗證（設定可關閉，預設要求）。</summary>
    internal static bool RequiresTwoFactor(string role, MembersOptions options) =>
        options.RequireMfaForAdmins && role is CustomerRoles.Owner or CustomerRoles.Admin;

    public bool IsRequired(ClaimsPrincipal principal) =>
        principal.FindFirst(ClaimTypes.Role)?.Value is { } role && RequiresTwoFactor(role, options.Value);

    public async Task<AdminOutcome<bool>> DisableAsync(ClaimsPrincipal principal, string password, string code, CancellationToken cancellationToken)
    {
        var info = Tenant();
        if (await UserOfAsync(principal) is not { } user)
            return AdminOutcome.NotFound<bool>(OutcomeCodes.AccountNotFound);

        if (!user.TwoFactorEnabled)
            return AdminOutcome.Invalid<bool>(OutcomeCodes.TwoFactorNotEnabled);

        // 角色要求的就不能關：能關，等於這個要求只是個建議。要關得由營運者改角色或設定。
        if (RequiresTwoFactor(user.Role, options.Value))
            return AdminOutcome.Invalid<bool>(OutcomeCodes.TwoFactorRequiredForRole);

        // 密碼與驗證碼都要對：只知道其中一個（看到密碼、或借到沒鎖的手機）的人，不能把保護關掉。
        // 兩者的失敗都計入鎖定，所以這裡不能成為猜測的後門。
        if (!await ConfirmAsync(user, code, password))
            return AdminOutcome.Invalid<bool>(OutcomeCodes.CredentialsIncorrect);

        await Users.SetTwoFactorEnabledAsync(user, false);
        await Users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey);
        await Users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.RecoveryCodes);
        await Users.UpdateSecurityStampAsync(user);
        await signIn.RefreshSignInAsync(user);
        await Audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalMfaDisabled, info.Id, "member", user.Id), cancellationToken);
        return AdminOutcome.Ok(true);
    }

    /// <summary>Identity 原生的驗證器 provider（RFC 6238，內建時間容許範圍）。</summary>
    protected override Task<bool> VerifyCodeAsync(ApplicationUser user, string? code) =>
        Users.VerifyTwoFactorTokenAsync(user, Users.Options.Tokens.AuthenticatorTokenProvider, (code ?? string.Empty).Replace(" ", string.Empty).Replace("-", string.Empty));

    protected override async Task OnEnabledAsync(ApplicationUser user, CancellationToken cancellationToken)
    {
        await signIn.RefreshSignInAsync(user);
        await Audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalMfaEnabled, Tenant().Id, "member", user.Id), cancellationToken);
    }

    protected override Task OnRecoveryCodesRegeneratedAsync(ApplicationUser user, CancellationToken cancellationToken) =>
        Audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalRecoveryCodesRegenerated, Tenant().Id, "member", user.Id), cancellationToken);

    public override async Task<bool> HasPendingSignInAsync() => await signIn.GetTwoFactorAuthenticationUserAsync() is not null;

    public override async Task<SignInStatus> CompleteSignInAsync(string code, bool isRecoveryCode, CancellationToken cancellationToken)
    {
        var info = Tenant();
        if (await signIn.GetTwoFactorAuthenticationUserAsync() is not { } user)
            return SignInStatus.Failed;

        // 在第一步之後、第二步之前被停用：不再給任何機會，待驗證 cookie 收回。
        if (user.Disabled)
        {
            await ClearPendingAsync();
            return SignInStatus.Failed;
        }

        // Identity 原生：驗證、失敗計數與鎖定、成功後歸零並簽發登入 cookie。
        var result = isRecoveryCode
            ? await signIn.TwoFactorRecoveryCodeSignInAsync((code ?? string.Empty).Trim())
            : await signIn.TwoFactorAuthenticatorSignInAsync(code ?? string.Empty, isPersistent: false, rememberClient: false);
        if (!result.Succeeded)
        {
            // 鎖定後不再給猜的機會，收回待驗證 cookie，要從密碼重來。
            if (result.IsLockedOut)
                await ClearPendingAsync();

            await Audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.PortalSignInFailed, info.Id, "member", user.Id, new Dictionary<string, object?>
                {
                    ["reason"] = result.IsLockedOut ? "locked_out" : isRecoveryCode ? "bad_recovery_code" : "bad_totp",
                }),
                cancellationToken);
            return SignInStatus.Failed;
        }

        // 稽核裡只記用了哪一種方式，絕不記碼本身。
        await Audit.AppendAsync(
            ActorOf(user),
            new AuditEntry(AuditActions.PortalSignedIn, info.Id, "member", user.Id, new Dictionary<string, object?> { ["method"] = isRecoveryCode ? "recovery_code" : "totp" }),
            cancellationToken);
        if (isRecoveryCode)
            await Audit.AppendAsync(ActorOf(user), new AuditEntry(AuditActions.PortalRecoveryCodeUsed, info.Id, "member", user.Id), cancellationToken);

        return user.MustChangePassword ? SignInStatus.MustChangePassword : SignInStatus.Succeeded;
    }

    private Task ClearPendingAsync() =>
        (http.HttpContext ?? throw new InvalidOperationException("No HTTP request.")).SignOutAsync(IdentityConstants.TwoFactorUserIdScheme);

    protected override async Task<ApplicationUser?> UserOfAsync(ClaimsPrincipal principal) =>
        principal.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is { } id ? await Users.FindByIdAsync(id) : null;

    private TenantInfo Tenant() =>
        tenant.MultiTenantContext?.TenantInfo ?? throw new InvalidOperationException("No tenant resolved for the portal request.");

    private static ClaimsPrincipal ActorOf(ApplicationUser user) =>
        new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, user.Id)], "portal"));
}