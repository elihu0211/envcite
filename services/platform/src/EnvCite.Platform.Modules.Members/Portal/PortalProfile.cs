using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members.Portal;

/// <inheritdoc cref="IPortalProfile"/>
internal sealed class PortalProfile(
    UserManager<ApplicationUser> users,
    SignInManager<ApplicationUser> signIn,
    IMultiTenantContextAccessor<TenantInfo> tenant,
    IAuditLog audit) : IPortalProfile
{
    public async Task<PortalProfileInfo?> GetAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        var found = await FindAsync(user);
        return found is null
            ? null
            : new PortalProfileInfo(
                found.Email ?? string.Empty,
                found.DisplayName,
                found.Role,
                found.TwoFactorEnabled,
                found.ManagedBy == DirectoryRoles.ManagedBy,
                found.AvatarData is not null ? found.AvatarVersion : 0);
    }

    public async Task<AdminOutcome<string?>> UpdateDisplayNameAsync(
        ClaimsPrincipal user, string? displayName, CancellationToken cancellationToken)
    {
        var found = await FindAsync(user);
        if (found is null || found.Disabled)
            return AdminOutcome.NotFound<string?>(OutcomeCodes.MemberNotFound);

        if (found.ManagedBy == DirectoryRoles.ManagedBy)
            return AdminOutcome.Conflict<string?>(OutcomeCodes.DirectoryMemberManaged);

        var normalized = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim();
        if (normalized is { Length: > 200 })
            return AdminOutcome.Invalid<string?>(OutcomeCodes.DisplayNameTooLong);

        if (string.Equals(found.DisplayName, normalized, StringComparison.Ordinal))
            return AdminOutcome.Ok(found.DisplayName);

        found.DisplayName = normalized;
        var updated = await users.UpdateAsync(found);
        if (!updated.Succeeded)
            return AdminOutcome.Invalid<string?>(OutcomeCodes.IdentityRejected);

        // 顯示名稱同時寫在登入 cookie 的 GivenName claim：更新後立刻重簽 cookie，頂欄與選單下一頁就同步。
        await signIn.RefreshSignInAsync(found);
        await audit.AppendAsync(
            user,
            new AuditEntry(
                AuditActions.PortalProfileUpdated,
                tenant.MultiTenantContext!.TenantInfo!.Id,
                "member",
                found.Id,
                new Dictionary<string, object?> { ["displayName"] = normalized }),
            cancellationToken);

        return AdminOutcome.Ok(normalized);
    }

    public async Task<AdminOutcome<int>> UpdateAvatarAsync(
        ClaimsPrincipal user, Stream imageStream, long contentLength, CancellationToken cancellationToken)
    {
        var found = await FindAsync(user);
        if (found is null || found.Disabled)
            return AdminOutcome.NotFound<int>(OutcomeCodes.MemberNotFound);

        var processed = await AvatarImageProcessor.NormalizeAsync(imageStream, contentLength, cancellationToken);
        if (!processed.IsOk)
            return AdminOutcome.Invalid<int>(processed.Code!);

        found.AvatarData = processed.Value;
        found.AvatarContentType = AvatarImageProcessor.OutputContentType;
        found.AvatarVersion++;

        var updated = await users.UpdateAsync(found);
        if (!updated.Succeeded)
            return AdminOutcome.Invalid<int>(OutcomeCodes.IdentityRejected);

        await signIn.RefreshSignInAsync(found);
        await audit.AppendAsync(
            user,
            new AuditEntry(
                AuditActions.PortalAvatarUpdated,
                tenant.MultiTenantContext!.TenantInfo!.Id,
                "member",
                found.Id,
                new Dictionary<string, object?> { ["version"] = found.AvatarVersion }),
            cancellationToken);

        return AdminOutcome.Ok(found.AvatarVersion);
    }

    public async Task<AdminOutcome<int>> RemoveAvatarAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        var found = await FindAsync(user);
        if (found is null || found.Disabled)
            return AdminOutcome.NotFound<int>(OutcomeCodes.MemberNotFound);

        if (found.AvatarData is null)
            return AdminOutcome.Ok(found.AvatarVersion);

        found.AvatarData = null;
        found.AvatarContentType = null;
        found.AvatarVersion++;

        var updated = await users.UpdateAsync(found);
        if (!updated.Succeeded)
            return AdminOutcome.Invalid<int>(OutcomeCodes.IdentityRejected);

        await signIn.RefreshSignInAsync(found);
        await audit.AppendAsync(
            user,
            new AuditEntry(
                AuditActions.PortalAvatarRemoved,
                tenant.MultiTenantContext!.TenantInfo!.Id,
                "member",
                found.Id,
                new Dictionary<string, object?> { ["version"] = found.AvatarVersion }),
            cancellationToken);

        return AdminOutcome.Ok(found.AvatarVersion);
    }

    public Task<PortalAvatar?> GetAvatarAsync(string userId, CancellationToken cancellationToken) =>
        users.Users
            .AsNoTracking()
            .Where(u => u.Id == userId && !u.Disabled && u.AvatarData != null && u.AvatarContentType != null)
            .Select(u => new PortalAvatar(u.AvatarData!, u.AvatarContentType!, u.AvatarVersion))
            .SingleOrDefaultAsync(cancellationToken);

    private Task<ApplicationUser?> FindAsync(ClaimsPrincipal user) =>
        user.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is { } id
            ? users.FindByIdAsync(id)
            : Task.FromResult<ApplicationUser?>(null);
}