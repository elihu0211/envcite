using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 管理營運者帳號（後台、命令列與本機開發的種子資料共用）。結構與 <see cref="MemberAdmin"/> 相同：
/// 一次性初始密碼只回傳一次、資料庫只存雜湊、不進日誌與稽核；邀請信只放連結、不放密碼（權杖由 Identity 即時產生，不存）；每個動作留稽核。營運者沒有租戶，所以不必綁定租戶 scope。
/// 沒有自助註冊：這是建立帳號的唯一途徑，呼叫端負責把初始密碼安全地交給對方。
/// </summary>
internal sealed class OperatorAdmin(
    IServiceScopeFactory scopes, IMailSender mail, IActivationMailer activation, IOptions<MailOptions> mailOptions, TimeProvider time) : IOperatorAdmin
{
    public async Task<AdminOutcome<IReadOnlyList<OperatorInfo>>> ListAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        var list = await users.Users.AsNoTracking().OrderBy(u => u.Email).ToListAsync(cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<OperatorInfo>>([.. list.Select(Info)]);
    }

    public async Task<AdminOutcome<IssuedOperatorPassword>> CreateAsync(
        ClaimsPrincipal actor, string email, string? displayName, CancellationToken cancellationToken)
    {
        if (NewAccountInput.Problem(email, displayName) is { } problem)
        {
            return AdminOutcome.Invalid<IssuedOperatorPassword>(problem);
        }

        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        var user = new OperatorUser
        {
            UserName = email.Trim(),
            Email = email.Trim(),
            DisplayName = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim(),
            MustChangePassword = true,
        };

        var password = OneTimePassword.New();
        var created = await users.CreateAsync(user, password);
        if (!created.Succeeded)
        {
            return IdentityOutcomes.CreateFailed<IssuedOperatorPassword>(created, OutcomeCodes.OperatorEmailTaken);
        }

        // 稽核的 Detail 只有 email，絕不放密碼（也沒有雜湊）。
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorCreated, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedOperatorPassword(Info(user), password));
    }

    public async Task<AdminOutcome<OperatorInvitation>> InviteAsync(
        ClaimsPrincipal actor, string email, string? displayName, CancellationToken cancellationToken)
    {
        if (!mail.Enabled)
        {
            return AdminOutcome.Conflict<OperatorInvitation>(OutcomeCodes.MailNotEnabled);
        }

        if (NewAccountInput.Problem(email, displayName) is { } problem)
        {
            return AdminOutcome.Invalid<OperatorInvitation>(problem);
        }

        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        var user = new OperatorUser
        {
            UserName = email.Trim(),
            Email = email.Trim(),
            DisplayName = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim(),
        };

        // 沒有密碼：在對方開啟連結設定之前，這個帳號登不進去；設好密碼後仍要依序綁定驗證器。
        var created = await users.CreateAsync(user);
        if (!created.Succeeded)
        {
            return IdentityOutcomes.CreateFailed<OperatorInvitation>(created, OutcomeCodes.OperatorEmailTaken);
        }

        var delivery = await SendActivationAsync(users, user, invitation: true, cancellationToken);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorInvited, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?>
            {
                ["email"] = user.Email,
                ["mail"] = delivery.AuditStatus(),
            }),
            cancellationToken);
        return AdminOutcome.Ok(new OperatorInvitation(Info(user), delivery));
    }

    public async Task<AdminOutcome<OperatorInvitation>> ResendInvitationAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken)
    {
        if (!mail.Enabled)
        {
            return AdminOutcome.Conflict<OperatorInvitation>(OutcomeCodes.MailNotEnabled);
        }

        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(operatorId) is not { } user)
        {
            return AdminOutcome.NotFound<OperatorInvitation>(OutcomeCodes.OperatorNotFound);
        }

        if (user.Disabled)
        {
            return AdminOutcome.Conflict<OperatorInvitation>(OutcomeCodes.AccountDisabled);
        }

        var invitation = !await users.HasPasswordAsync(user);
        if (!invitation && user.EmailConfirmed)
        {
            return AdminOutcome.Conflict<OperatorInvitation>(OutcomeCodes.AccountAlreadyActive);
        }

        var delivery = await SendActivationAsync(users, user, invitation, cancellationToken);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorInvitationResent, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?>
            {
                ["email"] = user.Email,
                ["kind"] = invitation ? "invitation" : "verification",
                ["mail"] = delivery.AuditStatus(),
            }),
            cancellationToken);
        return AdminOutcome.Ok(new OperatorInvitation(Info(user), delivery));
    }

    private async Task<MailDelivery> SendActivationAsync(
        UserManager<OperatorUser> users, OperatorUser user, bool invitation, CancellationToken cancellationToken)
    {
        var settings = mailOptions.Value;
        var code = await Activation.NewCodeAsync(users, user, revokeOld: invitation);
        var link = ActivationLinks.ForOperator(settings, user.Id.ToString(), code);
        return await activation.SendAsync(user.Email!, link, settings.LinkLifetime, tenantIdentifier: null, invitation, cancellationToken);
    }

    public async Task<AdminOutcome<IssuedOperatorPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(operatorId) is not { } user)
        {
            return AdminOutcome.NotFound<IssuedOperatorPassword>(OutcomeCodes.OperatorNotFound);
        }

        var password = OneTimePassword.New();
        user.PasswordHash = users.PasswordHasher.HashPassword(user, password);
        user.MustChangePassword = true;
        user.AccessFailedCount = 0;
        user.LockoutEnd = null;
        var updated = await users.UpdateAsync(user);
        if (!updated.Succeeded)
        {
            return IdentityOutcomes.Rejected<IssuedOperatorPassword>(updated.Errors.First());
        }

        // 讓既有的 session 失效：被重設通常是因為帳號可能已經不安全。
        await users.UpdateSecurityStampAsync(user);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorPasswordReset, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedOperatorPassword(Info(user), password));
    }

    public async Task<AdminOutcome<OperatorInfo>> ResetTwoFactorAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(operatorId) is not { } user)
        {
            return AdminOutcome.NotFound<OperatorInfo>(OutcomeCodes.OperatorNotFound);
        }

        await users.SetTwoFactorEnabledAsync(user, false);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.RecoveryCodes);
        user.LastTotpStep = 0;
        user.AccessFailedCount = 0;
        user.LockoutEnd = null;
        await users.UpdateAsync(user);

        // 讓既有的 session 失效：會需要重設通常是因為裝置遺失，可能已落入他人手中。
        await users.UpdateSecurityStampAsync(user);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorMfaReset, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(Info(user));
    }

    public async Task<AdminOutcome<OperatorInfo>> SetDisabledAsync(
        ClaimsPrincipal actor, string operatorId, bool disabled, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(operatorId) is not { } user)
        {
            return AdminOutcome.NotFound<OperatorInfo>(OutcomeCodes.OperatorNotFound);
        }

        if (user.Disabled == disabled)
        {
            return AdminOutcome.Ok(Info(user)); // 已經是那個狀態：不重複記錄
        }

        user.Disabled = disabled;
        await users.UpdateAsync(user);

        // 停用要讓對方所有 session 立刻失效（授權也直接檢查旗標）；重新啟用也換一次，舊的 cookie 不會復活。
        await users.UpdateSecurityStampAsync(user);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(disabled ? AuditActions.OperatorDisabled : AuditActions.OperatorEnabled, TargetType: "operator", TargetId: user.Id.ToString(),
                Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(Info(user));
    }

    private static IAuditLog Audit(AsyncServiceScope scope) => scope.ServiceProvider.GetRequiredService<IAuditLog>();

    private OperatorInfo Info(OperatorUser u) =>
        new(u.Id.ToString(), u.Email ?? string.Empty, u.DisplayName, u.MustChangePassword,
            u.LockoutEnd is { } end && end > time.GetUtcNow(), u.TwoFactorEnabled, u.Disabled, u.EmailConfirmed, string.IsNullOrEmpty(u.PasswordHash));
}