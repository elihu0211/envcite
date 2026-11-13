using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 群組對映角色的重算：受管成員的角色 = 他所屬、有對映的群組裡<b>最高</b>的那個角色；一個有對映的群組都沒有，就是租戶的預設角色。
/// 群組成員變動、群組被刪、或擁有者改了群組的對映時，對受影響的成員重算。必須在呼叫端已開的交易裡使用（成員的變更與稽核一起成功或一起失敗）。
/// </summary>
/// <remarks>
/// 只動「受目錄同步管理」的成員，而且不動擁有者（擁有者只由人指派）。租戶的預設角色改了不會回頭重算既有成員：
/// 預設角色只用於新建立的成員，以及不再屬於任何有對映群組的成員。
/// </remarks>
internal sealed class DirectoryGroupRoles(
    UserManager<ApplicationUser> users,
    PlatformDbContext db,
    IMultiTenantContextAccessor<TenantInfo> tenantAccessor,
    TimeProvider time)
{
    private static readonly string[] RoleField = ["role"];

    /// <summary>角色的高低：只用來在多個群組之間取最高。</summary>
    internal static int Rank(string? role) => role switch
    {
        CustomerRoles.Admin => 4,
        CustomerRoles.Developer => 3,
        CustomerRoles.Billing => 2,
        CustomerRoles.Viewer => 1,
        _ => 0,
    };

    /// <summary>
    /// 重算這些成員的角色；回傳實際改變了幾位。資料庫裡的群組與成員關係要先存好（這裡從資料庫讀），
    /// 改角色時換安全戳記（角色在 cookie 的 claim 裡，舊 session 失效），稽核事件在呼叫端的同一個交易裡存。
    /// </summary>
    public async Task<int> RecomputeAsync(ClaimsPrincipal actor, IEnumerable<string> userIds, CancellationToken cancellationToken)
    {
        var defaultRole = await db.DirectoryConnections.AsNoTracking().Select(c => c.DefaultRole).SingleOrDefaultAsync(cancellationToken)
            ?? CustomerRoles.Viewer;
        var changed = 0;
        foreach (var id in userIds.Distinct(StringComparer.Ordinal))
        {
            if (await users.FindByIdAsync(id) is not { ManagedBy: DirectoryRoles.ManagedBy } user || user.Role == CustomerRoles.Owner)
                continue;

            var mapped = await db.DirectoryGroupMembers
                .Where(m => m.UserId == id)
                .Join(db.DirectoryGroups.Where(g => g.Role != null), m => m.GroupId, g => g.Id, (_, g) => g.Role)
                .ToListAsync(cancellationToken);
            var role = mapped.Count == 0 ? defaultRole : mapped.OrderByDescending(Rank).First()!;
            if (role == user.Role)
                continue;

            var previous = user.Role;
            user.Role = role;
            user.SyncedAt = time.GetUtcNow();
            var updated = await users.UpdateAsync(user);
            if (!updated.Succeeded)
                throw new InvalidOperationException("Could not update a member's role from a group mapping.");

            await users.UpdateSecurityStampAsync(user);
            db.StageAudit(
                actor,
                new AuditEntry(AuditActions.MemberAttributeSynced, tenantAccessor.TenantInfo().Id, "member", user.Id, new Dictionary<string, object?>
                {
                    ["email"] = user.Email,
                    ["fields"] = RoleField,
                    ["previous_role"] = previous,
                    ["current_role"] = role,
                    ["via"] = "group",
                }),
                time);
            changed++;
        }

        return changed;
    }
}