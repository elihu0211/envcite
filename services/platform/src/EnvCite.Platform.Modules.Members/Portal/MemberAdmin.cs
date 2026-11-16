using System.Security.Claims;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members.Portal;

/// <summary>
/// 營運者管理租戶的成員。營運者的請求沒有 ambient tenant，所以每個操作經 <see cref="TenantScopes"/> 開一個綁好目標租戶的 scope，
/// 在那個 scope 裡解析的 <c>UserManager</c> 與 <c>PlatformDbContext</c> 就只看得到這個租戶的人。
/// 建立與重設密碼的核心在 <see cref="MemberCore"/>，與客戶自己的管理員共用。
/// </summary>
internal sealed class MemberAdmin(TenantScopes tenants) : IMemberAdmin
{
    public Task<Outcome<IReadOnlyList<MemberInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken) =>
        tenants.InAsync<IReadOnlyList<MemberInfo>>(tenantId, async scope =>
        {
            var core = scope.Get<MemberCore>();
            var list = await scope.Get<UserManager<ApplicationUser>>().Users.AsNoTracking().OrderBy(u => u.Email).ToListAsync(cancellationToken);
            return Outcomes.Ok<IReadOnlyList<MemberInfo>>([.. list.Select(core.Info)]);
        });

    public Task<Outcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, string tenantId, string email, string? displayName, string role, CancellationToken cancellationToken) =>
        tenants.InAsync<IssuedPassword>(tenantId, scope =>
            scope.Get<MemberCore>().CreateAsync(actor, scope.Tenant, email, displayName, role, cancellationToken));

    public Task<Outcome<MemberInvitation>> InviteAsync(
        ClaimsPrincipal actor, string tenantId, string email, string? displayName, string role, CancellationToken cancellationToken) =>
        tenants.InAsync<MemberInvitation>(tenantId, scope =>
            scope.Get<MemberCore>().InviteAsync(actor, scope.Tenant, email, displayName, role, cancellationToken));

    public Task<Outcome<MemberInvitation>> ResendInvitationAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken) =>
        tenants.InAsync<MemberInvitation>(tenantId, async scope =>
        {
            // 租戶的查詢濾鏡讓別的租戶的人在這裡找不到。
            var user = await scope.Get<UserManager<ApplicationUser>>().FindByIdAsync(userId);
            return user is null
                ? Outcomes.NotFound<MemberInvitation>(OutcomeCodes.MemberNotFound)
                : await scope.Get<MemberCore>().ResendInvitationAsync(actor, scope.Tenant, user, cancellationToken);
        });

    public Task<Outcome<IssuedPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken) =>
        tenants.InAsync<IssuedPassword>(tenantId, async scope =>
        {
            // 租戶的查詢濾鏡讓別的租戶的人在這裡找不到：不能用 A 租戶的 id 重設 B 租戶的人。
            var user = await scope.Get<UserManager<ApplicationUser>>().FindByIdAsync(userId);
            return user is null
                ? Outcomes.NotFound<IssuedPassword>(OutcomeCodes.MemberNotFound)
                : await scope.Get<MemberCore>().ResetPasswordAsync(actor, scope.Tenant, user, cancellationToken);
        });

    public Task<Outcome<MemberInfo>> SetDisabledAsync(
        ClaimsPrincipal actor, string tenantId, string userId, bool disabled, CancellationToken cancellationToken) =>
        tenants.InAsync<MemberInfo>(tenantId, async scope =>
        {
            var user = await scope.Get<UserManager<ApplicationUser>>().FindByIdAsync(userId);
            if (user is null)
                return Outcomes.NotFound<MemberInfo>(OutcomeCodes.MemberNotFound);

            // 受目錄同步管理的人，停用與啟用由身分來源決定；要由人處理先解除管理。
            return user.ManagedBy == DirectoryRoles.ManagedBy
                ? Outcomes.Conflict<MemberInfo>(OutcomeCodes.DirectoryMemberManaged)
                : Outcomes.Ok(await scope.Get<MemberCore>().SetDisabledAsync(actor, scope.Tenant, user, disabled, cancellationToken));
        });

    public Task<Outcome<MemberInfo>> ResetTwoFactorAsync(
        ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken) =>
        tenants.InAsync<MemberInfo>(tenantId, async scope =>
        {
            // 租戶的查詢濾鏡讓別的租戶的人在這裡找不到。
            var users = scope.Get<UserManager<ApplicationUser>>();
            var user = await users.FindByIdAsync(userId);
            if (user is null)
                return Outcomes.NotFound<MemberInfo>(OutcomeCodes.MemberNotFound);

            await users.SetTwoFactorEnabledAsync(user, false);
            await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey);
            await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.RecoveryCodes);
            user.LastTotpStep = 0;
            user.AccessFailedCount = 0;
            user.LockoutEnd = null;
            await users.UpdateAsync(user);

            // 讓既有的 session 失效：會需要重設通常是因為裝置遺失，可能已落入他人手中。
            await users.UpdateSecurityStampAsync(user);

            await scope.Get<IAuditLog>().AppendAsync(
                actor,
                new AuditEntry(AuditActions.MemberMfaReset, scope.Tenant.Id, "member", user.Id, new Dictionary<string, object?> { ["email"] = user.Email }),
                cancellationToken);

            return Outcomes.Ok(scope.Get<MemberCore>().Info(user));
        });
}