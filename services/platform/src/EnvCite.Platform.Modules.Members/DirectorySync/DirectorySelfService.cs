using System.Security.Claims;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <inheritdoc cref="IDirectorySelfService"/>
internal sealed class DirectorySelfService(UserManager<ApplicationUser> users, DirectoryManagement management) : IDirectorySelfService
{
    public Task<AdminOutcome<DirectoryStatus>> GetStatusAsync(ClaimsPrincipal actor, CancellationToken cancellationToken) =>
        OwnerAsync(actor, _ => management.StatusAsync(cancellationToken));

    public Task<AdminOutcome<DirectoryStatus>> ConfigureAsync(ClaimsPrincipal actor, DirectorySettings settings, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.ConfigureAsync(ActorOf(me), settings, cancellationToken));

    public Task<AdminOutcome<IssuedDirectoryToken>> IssueTokenAsync(
        ClaimsPrincipal actor, string name, DateTimeOffset? expiresAt, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.IssueTokenAsync(ActorOf(me), name, expiresAt, cancellationToken));

    public Task<AdminOutcome<DirectoryTokenInfo>> RevokeTokenAsync(ClaimsPrincipal actor, Guid tokenId, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.RevokeTokenAsync(ActorOf(me), tokenId, cancellationToken));

    public Task<AdminOutcome<MemberInfo>> ReleaseManagedAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.ReleaseManagedAsync(ActorOf(me), userId, cancellationToken));

    public Task<AdminOutcome<DirectoryGroupInfo>> SetGroupRoleAsync(ClaimsPrincipal actor, Guid groupId, string? role, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.SetGroupRoleAsync(ActorOf(me), groupId, role, cancellationToken));

    /// <summary>操作者以資料庫為準（不信 cookie 的 claim），而且必須是啟用中的擁有者。</summary>
    private async Task<AdminOutcome<T>> OwnerAsync<T>(ClaimsPrincipal actor, Func<ApplicationUser, Task<AdminOutcome<T>>> action)
    {
        if (actor.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is not { } id
            || await users.FindByIdAsync(id) is not { Disabled: false, Role: CustomerRoles.Owner } me)
        {
            return AdminOutcome.Forbidden<T>(OutcomeCodes.DirectoryOwnerOnly);
        }

        return await action(me);
    }

    private static ClaimsPrincipal ActorOf(ApplicationUser user) =>
        new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, user.Id)], "portal"));
}