using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members.Portal;

/// <inheritdoc cref="ITenantSelfService"/>
internal sealed class TenantSelfService(
    UserManager<ApplicationUser> users,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IMultiTenantStore<TenantInfo> store,
    IAuditLog audit) : ITenantSelfService
{
    public async Task<AdminOutcome<PortalTenant>> RenameAsync(
        ClaimsPrincipal actor, string name, CancellationToken cancellationToken)
    {
        var userId = actor.FindFirst(PlatformClaimTypes.PortalUserId)?.Value;
        var me = userId is null ? null : await users.FindByIdAsync(userId);
        // 角色以資料庫為準，不信 cookie 裡的 claim。
        if (me is null || me.Disabled || me.Role != CustomerRoles.Owner)
            return AdminOutcome.Forbidden<PortalTenant>(OutcomeCodes.TenantRenameForbidden);

        var normalized = (name ?? string.Empty).Trim();
        if (string.IsNullOrEmpty(normalized))
            return AdminOutcome.Invalid<PortalTenant>(OutcomeCodes.TenantNameRequired);

        if (normalized.Length > 200)
            return AdminOutcome.Invalid<PortalTenant>(OutcomeCodes.TenantNameTooLong);

        var current = tenant.MultiTenantContext?.TenantInfo;
        if (current is null || await store.GetAsync(current.Id) is not { } entity)
            return AdminOutcome.NotFound<PortalTenant>(OutcomeCodes.TenantNotFound);

        if (string.Equals(entity.Name, normalized, StringComparison.Ordinal))
            return AdminOutcome.Ok(new PortalTenant(entity.Id, entity.Identifier, entity.Name));

        var previous = entity.Name;
        var updated = new TenantInfo
        {
            Id = entity.Id,
            Identifier = entity.Identifier,
            Name = normalized,
        };
        await store.UpdateAsync(updated);

        await audit.AppendAsync(
            actor,
            new AuditEntry(
                AuditActions.TenantRenamed,
                entity.Id,
                "tenant",
                entity.Id,
                new Dictionary<string, object?> { ["previous"] = previous, ["current"] = normalized }),
            cancellationToken);

        return AdminOutcome.Ok(new PortalTenant(updated.Id, updated.Identifier, updated.Name));
    }
}