using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 建立成員與重設密碼的核心。營運者（<see cref="MemberAdmin"/>，替目標租戶開 scope）與客戶自己的管理員
/// （<see cref="MemberSelfService"/>，租戶是請求的租戶）共用，所以驗證、一次性密碼、稽核只有一份。
/// 呼叫端負責「誰可以做」；這裡只管「怎麼做」。必須在已經綁定租戶的 scope 裡使用。
/// </summary>
internal sealed class MemberCore(
    UserManager<ApplicationUser> users, IAuditLog audit, IMailSender mail, IActivationMailer activation, IOptions<MailOptions> mailOptions, TimeProvider time)
{
    public async Task<AdminOutcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, TenantInfo tenant, string email, string? displayName, string role, CancellationToken cancellationToken)
    {
        if (NewAccountInput.Problem(email, displayName, role) is { } problem)
            return AdminOutcome.Invalid<IssuedPassword>(problem);

        var user = new ApplicationUser
        {
            UserName = email.Trim(),
            Email = email.Trim(),
            DisplayName = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim(),
            Role = role,
            MustChangePassword = true,
        };

        var password = OneTimePassword.New();
        var created = await users.CreateAsync(user, password);
        if (!created.Succeeded)
            return IdentityOutcomes.CreateFailed<IssuedPassword>(created, OutcomeCodes.MemberEmailTaken);

        // 稽核的 Detail 只有 email 與角色，絕不放密碼（也沒有雜湊）。
        await audit.AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberCreated, tenant.Id, "member", user.Id, new Dictionary<string, object?>
            {
                ["email"] = user.Email,
                ["role"] = user.Role,
            }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedPassword(Info(user), password));
    }

    /// <summary>
    /// 邀請：建立沒有密碼的成員，寄單次、限時的連結。寄信失敗不讓建立失敗（成員已在，可以重寄或改用暫時密碼）。
    /// 稽核只有信箱（登入名稱）、角色與寄信結果，沒有連結。
    /// </summary>
    public async Task<AdminOutcome<MemberInvitation>> InviteAsync(
        ClaimsPrincipal actor, TenantInfo tenant, string email, string? displayName, string role, CancellationToken cancellationToken)
    {
        if (!mail.Enabled)
            return AdminOutcome.Conflict<MemberInvitation>(OutcomeCodes.MailNotEnabled);

        if (NewAccountInput.Problem(email, displayName, role) is { } problem)
            return AdminOutcome.Invalid<MemberInvitation>(problem);

        var user = new ApplicationUser
        {
            UserName = email.Trim(),
            Email = email.Trim(),
            DisplayName = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim(),
            Role = role,
        };

        // 沒有密碼：在對方開啟連結設定之前，這個帳號登不進去。
        var created = await users.CreateAsync(user);
        if (!created.Succeeded)
            return IdentityOutcomes.CreateFailed<MemberInvitation>(created, OutcomeCodes.MemberEmailTaken);

        var delivery = await SendActivationAsync(user, tenant, invitation: true, cancellationToken);
        await audit.AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberInvited, tenant.Id, "member", user.Id, new Dictionary<string, object?>
            {
                ["email"] = user.Email,
                ["role"] = user.Role,
                ["mail"] = delivery.AuditStatus(),
            }),
            cancellationToken);
        return AdminOutcome.Ok(new MemberInvitation(Info(user), delivery));
    }

    /// <summary>重寄：沒有密碼的重發邀請（舊連結失效）；有密碼但信箱沒驗證的寄驗證連結；都完成了則 Conflict。</summary>
    public async Task<AdminOutcome<MemberInvitation>> ResendInvitationAsync(
        ClaimsPrincipal actor, TenantInfo tenant, ApplicationUser user, CancellationToken cancellationToken)
    {
        if (!mail.Enabled)
            return AdminOutcome.Conflict<MemberInvitation>(OutcomeCodes.MailNotEnabled);

        if (user.Disabled)
            return AdminOutcome.Conflict<MemberInvitation>(OutcomeCodes.AccountDisabled);

        var invitation = !await users.HasPasswordAsync(user);
        if (!invitation && user.EmailConfirmed)
            return AdminOutcome.Conflict<MemberInvitation>(OutcomeCodes.AccountAlreadyActive);

        var delivery = await SendActivationAsync(user, tenant, invitation, cancellationToken);
        await audit.AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberInvitationResent, tenant.Id, "member", user.Id, new Dictionary<string, object?>
            {
                ["email"] = user.Email,
                ["kind"] = invitation ? "invitation" : "verification",
                ["mail"] = delivery.AuditStatus(),
            }),
            cancellationToken);
        return AdminOutcome.Ok(new MemberInvitation(Info(user), delivery));
    }

    /// <summary>目錄同步建立（或重新啟用）一位沒有密碼的成員後寄邀請連結；先前的連結失效。結果不使呼叫端失敗，由呼叫端記稽核。</summary>
    internal Task<MailDelivery> SendInvitationAsync(ApplicationUser user, TenantInfo tenant, CancellationToken cancellationToken) =>
        SendActivationAsync(user, tenant, invitation: true, cancellationToken);

    private async Task<MailDelivery> SendActivationAsync(ApplicationUser user, TenantInfo tenant, bool invitation, CancellationToken cancellationToken)
    {
        var settings = mailOptions.Value;
        var code = await Activation.NewCodeAsync(users, user, revokeOld: invitation);
        var link = ActivationLinks.ForMember(settings, tenant.Identifier!, user.Id, code);
        return await activation.SendAsync(user.Email!, link, settings.LinkLifetime, tenant.Identifier, invitation, cancellationToken);
    }

    public async Task<AdminOutcome<IssuedPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, TenantInfo tenant, ApplicationUser user, CancellationToken cancellationToken)
    {
        var password = OneTimePassword.New();
        user.PasswordHash = users.PasswordHasher.HashPassword(user, password);
        user.MustChangePassword = true;
        user.AccessFailedCount = 0;
        user.LockoutEnd = null;
        var updated = await users.UpdateAsync(user);
        if (!updated.Succeeded)
            return IdentityOutcomes.Rejected<IssuedPassword>(updated.Errors.First());

        // 讓既有的 session 失效：被重設通常是因為帳號可能已經不安全。
        await users.UpdateSecurityStampAsync(user);

        await audit.AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberPasswordReset, tenant.Id, "member", user.Id, new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedPassword(Info(user), password));
    }

    /// <summary>停用或重新啟用（冪等）。換安全戳記讓所有 session 立刻失效，留稽核。</summary>
    public async Task<MemberInfo> SetDisabledAsync(
        ClaimsPrincipal actor, TenantInfo tenant, ApplicationUser target, bool disabled, CancellationToken cancellationToken)
    {
        if (target.Disabled == disabled)
        {
            return Info(target); // 已經是那個狀態：不重複記錄
        }

        target.Disabled = disabled;
        await users.UpdateAsync(target);

        // 停用要讓對方所有 session 立刻失效；重新啟用也換一次，確保是乾淨的狀態。
        await users.UpdateSecurityStampAsync(target);
        await audit.AppendAsync(
            actor,
            new AuditEntry(disabled ? AuditActions.MemberDisabled : AuditActions.MemberEnabled, tenant.Id, "member", target.Id,
                new Dictionary<string, object?> { ["email"] = target.Email }),
            cancellationToken);
        return Info(target);
    }

    public MemberInfo Info(ApplicationUser u) =>
        new(u.Id, u.Email ?? string.Empty, u.DisplayName, u.Role, u.MustChangePassword,
            u.LockoutEnd is { } end && end > time.GetUtcNow(), u.TwoFactorEnabled, u.Disabled, u.EmailConfirmed, string.IsNullOrEmpty(u.PasswordHash),
            u.ManagedBy == DirectoryRoles.ManagedBy);
}