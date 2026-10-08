using System.Security.Claims;

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
/// 建立與重設密碼的核心在 <see cref="MemberCore"/>，與客戶自己的管理員共用。
/// </summary>
internal sealed class MemberAdmin(IServiceScopeFactory scopes) : IMemberAdmin
{
    // Identity 把驗證器金鑰與復原碼存成使用者的 token（固定的提供者名稱與 token 名稱）。
    private const string TwoFactorTokenProvider = "[AspNetUserStore]";
    private const string AuthenticatorKeyToken = "AuthenticatorKey";
    private const string RecoveryCodesToken = "RecoveryCodes";

    public async Task<AdminOutcome<IReadOnlyList<MemberInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await FindTenantAsync(scope, tenantId) is not { } found)
        {
            return AdminOutcome.NotFound<IReadOnlyList<MemberInfo>>("Tenant not found.");
        }

        Bind(scope, found);

        var users = scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
        var core = scope.ServiceProvider.GetRequiredService<MemberCore>();
        var list = await users.Users.AsNoTracking().OrderBy(u => u.Email).ToListAsync(cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<MemberInfo>>([.. list.Select(core.Info)]);
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
        return await scope.ServiceProvider.GetRequiredService<MemberCore>()
            .CreateAsync(actor, tenant, email, displayName, role, cancellationToken);
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
        var user = await scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>().FindByIdAsync(userId);
        return user is null
            ? AdminOutcome.NotFound<IssuedPassword>("Member not found.")
            : await scope.ServiceProvider.GetRequiredService<MemberCore>().ResetPasswordAsync(actor, tenant, user, cancellationToken);
    }

    public async Task<AdminOutcome<MemberInfo>> SetDisabledAsync(
        ClaimsPrincipal actor, string tenantId, string userId, bool disabled, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await FindTenantAsync(scope, tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<MemberInfo>("Tenant not found.");
        }

        Bind(scope, tenant);
        var user = await scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>().FindByIdAsync(userId);
        return user is null
            ? AdminOutcome.NotFound<MemberInfo>("Member not found.")
            : AdminOutcome.Ok(await scope.ServiceProvider.GetRequiredService<MemberCore>().SetDisabledAsync(actor, tenant, user, disabled, cancellationToken));
    }

    public async Task<AdminOutcome<MemberInfo>> ResetTwoFactorAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await FindTenantAsync(scope, tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<MemberInfo>("Tenant not found.");
        }

        Bind(scope, tenant);

        // 租戶的查詢濾鏡讓別的租戶的人在這裡找不到。
        var users = scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
        var user = await users.FindByIdAsync(userId);
        if (user is null)
        {
            return AdminOutcome.NotFound<MemberInfo>("Member not found.");
        }

        await users.SetTwoFactorEnabledAsync(user, false);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokenProvider, AuthenticatorKeyToken);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokenProvider, RecoveryCodesToken);
        user.LastTotpStep = 0;
        user.AccessFailedCount = 0;
        user.LockoutEnd = null;
        await users.UpdateAsync(user);

        // 讓既有的 session 失效：會需要重設通常是因為裝置遺失，可能已落入他人手中。
        await users.UpdateSecurityStampAsync(user);

        await scope.ServiceProvider.GetRequiredService<IAuditLog>().AppendAsync(
            actor,
            new AuditEntry(AuditActions.MemberMfaReset, tenant.Id, "member", user.Id, new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(scope.ServiceProvider.GetRequiredService<MemberCore>().Info(user));
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
}