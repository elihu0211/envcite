using System.Security.Claims;

using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <inheritdoc cref="IDirectorySelfService"/>
internal sealed class DirectorySelfService(UserManager<ApplicationUser> users, DirectoryManagement management) : IDirectorySelfService
{
    public Task<Outcome<DirectoryStatus>> GetStatusAsync(ClaimsPrincipal actor, CancellationToken cancellationToken) =>
        OwnerAsync(actor, _ => management.StatusAsync(cancellationToken));

    public Task<Outcome<DirectoryStatus>> ConfigureAsync(ClaimsPrincipal actor, DirectorySettings settings, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.ConfigureAsync(ActorOf(me), settings, cancellationToken));

    public Task<Outcome<IssuedDirectoryToken>> IssueTokenAsync(
        ClaimsPrincipal actor, string name, DateTimeOffset? expiresAt, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.IssueTokenAsync(ActorOf(me), name, expiresAt, cancellationToken));

    public Task<Outcome<DirectoryTokenInfo>> RevokeTokenAsync(ClaimsPrincipal actor, Guid tokenId, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.RevokeTokenAsync(ActorOf(me), tokenId, cancellationToken));

    public Task<Outcome<MemberInfo>> ReleaseManagedAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.ReleaseManagedAsync(ActorOf(me), userId, cancellationToken));

    public Task<Outcome<DirectoryGroupInfo>> SetGroupRoleAsync(ClaimsPrincipal actor, Guid groupId, string? role, CancellationToken cancellationToken) =>
        OwnerAsync(actor, me => management.SetGroupRoleAsync(ActorOf(me), groupId, role, cancellationToken));

    /// <summary>操作者以資料庫為準（不信 cookie 的 claim），而且必須是啟用中的擁有者。</summary>
    private async Task<Outcome<T>> OwnerAsync<T>(ClaimsPrincipal actor, Func<ApplicationUser, Task<Outcome<T>>> action)
    {
        if (actor.FindFirst(PlatformClaimTypes.PortalUserId)?.Value is not { } id
            || await users.FindByIdAsync(id) is not { Disabled: false, Role: CustomerRoles.Owner } me)
        {
            return Outcomes.Forbidden<T>(OutcomeCodes.DirectoryOwnerOnly);
        }

        return await action(me);
    }

    private static ClaimsPrincipal ActorOf(ApplicationUser user) =>
        new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, user.Id)], "portal"));
}