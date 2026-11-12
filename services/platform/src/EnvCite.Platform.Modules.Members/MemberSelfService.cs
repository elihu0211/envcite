using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 階層規則。純函式，所以每一格都有單元測試。擁有者管全部；管理員只管開發者、帳務、唯讀。
/// 「能指派的角色」與「能管理的角色」是同一組：能管理誰，就只能把人放到那一層（不能升到自己之上）。
/// </summary>
internal static class MemberPolicy
{
    private static readonly string[] BelowAdmin = [CustomerRoles.Developer, CustomerRoles.Billing, CustomerRoles.Viewer];

    public static IReadOnlyList<string> Grantable(string? actorRole) => actorRole switch
    {
        CustomerRoles.Owner => CustomerRoles.All,
        CustomerRoles.Admin => BelowAdmin,
        _ => [],
    };

    public static bool CanManage(string? actorRole, string targetRole) => Grantable(actorRole).Contains(targetRole);

    public static bool IsManager(string? role) => role is CustomerRoles.Owner or CustomerRoles.Admin;
}

/// <inheritdoc cref="IMemberSelfService"/>
internal sealed class MemberSelfService(
    UserManager<ApplicationUser> users,
    MemberCore core,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IAuditLog audit) : IMemberSelfService
{
    public IReadOnlyList<string> GrantableRoles(ClaimsPrincipal actor) =>
        MemberPolicy.Grantable(actor.FindFirst(ClaimTypes.Role)?.Value);

    public async Task<AdminOutcome<IReadOnlyList<TeamMember>>> ListAsync(ClaimsPrincipal actor, CancellationToken cancellationToken)
    {
        if (await ActorAsync(actor) is not { } me || !MemberPolicy.IsManager(me.Role))
        {
            return AdminOutcome.Forbidden<IReadOnlyList<TeamMember>>(OutcomeCodes.MemberManageForbidden);
        }

        var list = await users.Users.AsNoTracking().OrderBy(u => u.Email).ToListAsync(cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<TeamMember>>(
            [.. list.Select(u => new TeamMember(core.Info(u), u.Id == me.Id, u.Id != me.Id && MemberPolicy.CanManage(me.Role, u.Role)))]);
    }

    public async Task<AdminOutcome<IssuedPassword>> CreateAsync(
        ClaimsPrincipal actor, string email, string? displayName, string role, CancellationToken cancellationToken)
    {
        if (await ActorAsync(actor) is not { } me || !MemberPolicy.IsManager(me.Role))
        {
            return AdminOutcome.Forbidden<IssuedPassword>(OutcomeCodes.MemberAddForbidden);
        }

        if (!MemberPolicy.Grantable(me.Role).Contains(role))
        {
            return AdminOutcome.Forbidden<IssuedPassword>(OutcomeCodes.MemberAddRoleForbidden);
        }

        return await core.CreateAsync(ActorOf(me), Tenant(), email, displayName, role, cancellationToken);
    }

    public async Task<AdminOutcome<MemberInvitation>> InviteAsync(
        ClaimsPrincipal actor, string email, string? displayName, string role, CancellationToken cancellationToken)
    {
        if (await ActorAsync(actor) is not { } me || !MemberPolicy.IsManager(me.Role))
        {
            return AdminOutcome.Forbidden<MemberInvitation>(OutcomeCodes.MemberAddForbidden);
        }

        if (!MemberPolicy.Grantable(me.Role).Contains(role))
        {
            return AdminOutcome.Forbidden<MemberInvitation>(OutcomeCodes.MemberAddRoleForbidden);
        }

        return await core.InviteAsync(ActorOf(me), Tenant(), email, displayName, role, cancellationToken);
    }

    public async Task<AdminOutcome<MemberInvitation>> ResendInvitationAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken)
    {
        var (me, target, refusal) = await ResolveAsync<MemberInvitation>(actor, userId);
        return refusal ?? await core.ResendInvitationAsync(ActorOf(me!), Tenant(), target!, cancellationToken);
    }

    public async Task<AdminOutcome<MemberInfo>> SetDisabledAsync(
        ClaimsPrincipal actor, string userId, bool disabled, CancellationToken cancellationToken)
    {
        var (me, target, refusal) = await ResolveAsync<MemberInfo>(actor, userId);
        if (refusal is not null)
        {
            return refusal;
        }

        return AdminOutcome.Ok(await core.SetDisabledAsync(ActorOf(me!), Tenant(), target!, disabled, cancellationToken));
    }

    public async Task<AdminOutcome<MemberInfo>> ChangeRoleAsync(
        ClaimsPrincipal actor, string userId, string role, CancellationToken cancellationToken)
    {
        var (me, target, refusal) = await ResolveAsync<MemberInfo>(actor, userId);
        if (refusal is not null)
        {
            return refusal;
        }

        if (!CustomerRoles.All.Contains(role))
        {
            return AdminOutcome.Invalid<MemberInfo>(OutcomeCodes.RoleInvalid);
        }

        // 能管理對方現在的角色，也只能放到自己能指派的角色：管理員不能把人升成管理員或擁有者。
        if (!MemberPolicy.Grantable(me!.Role).Contains(role))
        {
            return AdminOutcome.Forbidden<MemberInfo>(OutcomeCodes.MemberAssignRoleForbidden);
        }

        if (target!.Role == role)
        {
            return AdminOutcome.Ok(core.Info(target));
        }

        var previous = target.Role;
        target.Role = role;
        await users.UpdateAsync(target);

        // 角色在 cookie 的 claim 裡：換安全戳記讓舊 session 失效，下次登入才拿到新角色（也才會被新角色的兩步驟要求套用）。
        await users.UpdateSecurityStampAsync(target);
        await audit.AppendAsync(
            ActorOf(me),
            new AuditEntry(AuditActions.MemberRoleChanged, Tenant().Id, "member", target.Id,
                new Dictionary<string, object?> { ["email"] = target.Email, ["previous"] = previous, ["current"] = role }),
            cancellationToken);
        return AdminOutcome.Ok(core.Info(target));
    }

    public async Task<AdminOutcome<IssuedPassword>> ResetPasswordAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken)
    {
        var (me, target, refusal) = await ResolveAsync<IssuedPassword>(actor, userId);
        return refusal ?? await core.ResetPasswordAsync(ActorOf(me!), Tenant(), target!, cancellationToken);
    }

    /// <summary>操作者（以資料庫為準）與目標，並套用共通的規則：不能動自己、要能管理對方的角色。</summary>
    private async Task<(ApplicationUser? Me, ApplicationUser? Target, AdminOutcome<T>? Refusal)> ResolveAsync<T>(ClaimsPrincipal actor, string userId)
    {
        if (await ActorAsync(actor) is not { } me || !MemberPolicy.IsManager(me.Role))
        {
            return (null, null, AdminOutcome.Forbidden<T>(OutcomeCodes.MemberManageForbidden));
        }

        // 租戶的查詢濾鏡讓別的租戶的人在這裡找不到。
        if (await users.FindByIdAsync(userId) is not { } target)
        {
            return (me, null, AdminOutcome.NotFound<T>(OutcomeCodes.MemberNotFound));
        }

        if (target.Id == me.Id)
        {
            return (me, target, AdminOutcome.Forbidden<T>(OutcomeCodes.MemberManageSelfForbidden));
        }

        if (!MemberPolicy.CanManage(me.Role, target.Role))
        {
            return (me, target, AdminOutcome.Forbidden<T>(OutcomeCodes.MemberManageTargetForbidden));
        }

        return (me, target, null);
    }

    private async Task<ApplicationUser?> ActorAsync(ClaimsPrincipal actor) =>
        actor.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is { } id && await users.FindByIdAsync(id) is { Disabled: false } me ? me : null;

    private TenantInfo Tenant() =>
        tenant.MultiTenantContext?.TenantInfo ?? throw new InvalidOperationException("No tenant resolved for the portal request.");

    private static ClaimsPrincipal ActorOf(ApplicationUser user) =>
        new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, user.Id)], "portal"));
}