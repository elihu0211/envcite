using System.Net.Mail;
using System.Security.Claims;
using System.Security.Cryptography;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Members;

/// <summary>
/// 營運者管理租戶的成員。營運者的請求沒有 ambient tenant，所以每次自己開 scope，把租戶設成目標租戶，
/// 之後在那個 scope 裡解析的 <c>UserManager</c> 與 <c>PlatformDbContext</c> 就只看得到這個租戶的人。
/// </summary>
internal sealed class MemberAdmin(IServiceScopeFactory scopes, TimeProvider time) : IMemberAdmin
{
    // 去掉容易看錯的字元（0/O、1/l/I）。16 個字元約 92 位元，遠高於需求；客戶第一次登入就要改掉。
    private const string PasswordAlphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private const int InitialPasswordLength = 16;

    public async Task<AdminOutcome<IReadOnlyList<MemberInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await FindTenantAsync(scope, tenantId) is not { } found)
        {
            return AdminOutcome.NotFound<IReadOnlyList<MemberInfo>>("Tenant not found.");
        }

        Bind(scope, found);

        var users = scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
        var now = time.GetUtcNow();
        var list = await users.Users.AsNoTracking().OrderBy(u => u.Email).ToListAsync(cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<MemberInfo>>([.. list.Select(u => Info(u, now))]);
    }

    public async Task<AdminOutcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, string tenantId, string email, string? displayName, string role, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await FindTenantAsync(scope, tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<IssuedPassword>("Tenant not found.");
        }

        Bind(scope, tenant);

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

        var users = scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
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
        await scope.ServiceProvider.GetRequiredService<IAuditLog>().AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberCreated, tenant.Id, "member", user.Id, new Dictionary<string, object?>
            {
                ["email"] = user.Email,
                ["role"] = user.Role,
            }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedPassword(Info(user, time.GetUtcNow()), password));
    }

    public async Task<AdminOutcome<IssuedPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await FindTenantAsync(scope, tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<IssuedPassword>("Tenant not found.");
        }

        Bind(scope, tenant);

        // 租戶的查詢濾鏡讓別的租戶的人在這裡找不到：不能用 A 租戶的 id 重設 B 租戶的人。
        var users = scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
        var user = await users.FindByIdAsync(userId);
        if (user is null)
        {
            return AdminOutcome.NotFound<IssuedPassword>("Member not found.");
        }

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

        await scope.ServiceProvider.GetRequiredService<IAuditLog>().AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberPasswordReset, tenant.Id, "member", user.Id, new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedPassword(Info(user, time.GetUtcNow()), password));
    }

    private static async Task<TenantInfo?> FindTenantAsync(AsyncServiceScope scope, string tenantId) =>
        await scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>().GetAsync(tenantId);

    /// <summary>
    /// 把這個 scope 的租戶設成目標租戶。必須在解析 <c>UserManager</c>／<c>PlatformDbContext</c> 之前，
    /// 而且<b>不能放進 async 方法</b>：租戶 context 是 AsyncLocal，在 async 方法裡設定，方法一返回就丟掉，
    /// 呼叫端的 DbContext 會捕捉到沒有租戶。
    /// </summary>
    private static void Bind(AsyncServiceScope scope, TenantInfo tenant) =>
        scope.ServiceProvider.GetRequiredService<IMultiTenantContextSetter>().MultiTenantContext = new MultiTenantContext<TenantInfo>(tenant);

    private static string NewPassword() => RandomNumberGenerator.GetString(PasswordAlphabet, InitialPasswordLength);

    private static bool IsValidEmail(string? email) =>
        !string.IsNullOrWhiteSpace(email)
        && email.Length <= 254
        && MailAddress.TryCreate(email.Trim(), out var parsed)
        && parsed.Address == email.Trim();

    private static MemberInfo Info(ApplicationUser u, DateTimeOffset now) =>
        new(u.Id, u.Email ?? string.Empty, u.DisplayName, u.Role, u.MustChangePassword, u.LockoutEnd is { } end && end > now);
}