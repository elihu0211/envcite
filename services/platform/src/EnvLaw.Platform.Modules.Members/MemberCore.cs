using System.Net.Mail;
using System.Security.Claims;
using System.Security.Cryptography;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

namespace EnvLaw.Platform.Modules.Members;

/// <summary>
/// 建立成員與重設密碼的核心。營運者（<see cref="MemberAdmin"/>，替目標租戶開 scope）與客戶自己的管理員
/// （<see cref="MemberSelfService"/>，租戶是請求的租戶）共用，所以驗證、一次性密碼、稽核只有一份。
/// 呼叫端負責「誰可以做」；這裡只管「怎麼做」。必須在已經綁定租戶的 scope 裡使用。
/// </summary>
internal sealed class MemberCore(UserManager<ApplicationUser> users, IAuditLog audit, TimeProvider time)
{
    // 去掉容易看錯的字元（0/O、1/l/I）。16 個字元約 92 位元，遠高於需求；成員第一次登入就要改掉。
    private const string PasswordAlphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private const int InitialPasswordLength = 16;

    public async Task<AdminOutcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, TenantInfo tenant, string email, string? displayName, string role, CancellationToken cancellationToken)
    {
        if (!IsValidEmail(email))
        {
            return AdminOutcome.Invalid<IssuedPassword>("Email 格式不正確。");
        }

        if (!CustomerRoles.All.Contains(role))
        {
            return AdminOutcome.Invalid<IssuedPassword>("角色不在允許的範圍內。");
        }

        if (displayName is { Length: > 200 })
        {
            return AdminOutcome.Invalid<IssuedPassword>("顯示名稱最長 200 字元。");
        }

        var user = new ApplicationUser
        {
            UserName = email.Trim(),
            Email = email.Trim(),
            DisplayName = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim(),
            Role = role,
            MustChangePassword = true,
        };

        var password = NewPassword();
        var created = await users.CreateAsync(user, password);
        if (!created.Succeeded)
        {
            return created.Errors.Any(e => e.Code is nameof(IdentityErrorDescriber.DuplicateUserName) or nameof(IdentityErrorDescriber.DuplicateEmail))
                ? AdminOutcome.Conflict<IssuedPassword>("這個租戶已經有這個 email 的成員。")
                : AdminOutcome.Invalid<IssuedPassword>(created.Errors.First().Description);
        }

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

    public async Task<AdminOutcome<IssuedPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, TenantInfo tenant, ApplicationUser user, CancellationToken cancellationToken)
    {
        var password = NewPassword();
        user.PasswordHash = users.PasswordHasher.HashPassword(user, password);
        user.MustChangePassword = true;
        user.AccessFailedCount = 0;
        user.LockoutEnd = null;
        var updated = await users.UpdateAsync(user);
        if (!updated.Succeeded)
        {
            return AdminOutcome.Invalid<IssuedPassword>(updated.Errors.First().Description);
        }

        // 讓既有的 session 失效：被重設通常是因為帳號可能已經不安全。
        await users.UpdateSecurityStampAsync(user);

        await audit.AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberPasswordReset, tenant.Id, "member", user.Id, new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedPassword(Info(user), password));
    }

    public MemberInfo Info(ApplicationUser u) =>
        new(u.Id, u.Email ?? string.Empty, u.DisplayName, u.Role, u.MustChangePassword,
            u.LockoutEnd is { } end && end > time.GetUtcNow(), u.TwoFactorEnabled, u.Disabled);

    private static string NewPassword() => RandomNumberGenerator.GetString(PasswordAlphabet, InitialPasswordLength);

    private static bool IsValidEmail(string? email) =>
        !string.IsNullOrWhiteSpace(email)
        && email.Length <= 254
        && MailAddress.TryCreate(email.Trim(), out var parsed)
        && parsed.Address == email.Trim();
}