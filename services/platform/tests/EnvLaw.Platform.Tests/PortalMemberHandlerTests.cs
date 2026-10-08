using System.Security.Claims;

using EnvLaw.Platform.Modules.Members;
using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authorization;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// 入口授權的每一道檢查各自單獨測：端對端的測試裡，使用者表的租戶 filter 會先擋下別租戶的 cookie，
/// 把這裡的檢查蓋住——拿掉它端對端測試照樣通過。所以直接測 handler，讓每一層都有自己的證明。
/// </summary>
public sealed class PortalMemberHandlerTests
{
    private static AsyncLocalMultiTenantContextAccessor<TenantInfo> Accessor(string? resolvedTenant)
    {
        var accessor = new AsyncLocalMultiTenantContextAccessor<TenantInfo>();
        ((IMultiTenantContextSetter)accessor).MultiTenantContext = new MultiTenantContext<TenantInfo>(
            resolvedTenant is null ? null : new TenantInfo { Id = "id", Identifier = resolvedTenant });
        return accessor;
    }

    private static ClaimsPrincipal Member(string? tenant, bool passwordChangePending = false, bool withUserId = true)
    {
        var claims = new List<Claim>();
        if (withUserId)
        {
            claims.Add(new Claim(PlatformClaimTypes.PortalUserId, "user-1"));
        }

        if (tenant is not null)
        {
            claims.Add(new Claim(PlatformClaimTypes.TenantIdentifier, tenant));
        }

        if (passwordChangePending)
        {
            claims.Add(new Claim(PlatformClaimTypes.PasswordChangeRequired, "true"));
        }

        return new ClaimsPrincipal(new ClaimsIdentity(claims, "test"));
    }

    private static async Task<bool> SucceedsAsync(string? resolvedTenant, ClaimsPrincipal user, bool allowPending)
    {
        var requirement = new PortalMemberRequirement(allowPending);
        var context = new AuthorizationHandlerContext([requirement], user, null);
        await new PortalMemberHandler(Accessor(resolvedTenant)).HandleAsync(context);
        return context.HasSucceeded;
    }

    [Fact]
    public async Task AMemberOfTheResolvedTenantPasses() =>
        Assert.True(await SucceedsAsync("acme", Member("acme"), allowPending: false));

    [Fact]
    public async Task ASessionForAnotherTenantFailsEvenWithEveryOtherClaimInOrder() =>
        Assert.False(await SucceedsAsync("acme", Member("other"), allowPending: true));

    [Fact]
    public async Task TenantNamesAreComparedExactly() =>
        Assert.False(await SucceedsAsync("acme", Member("ACME"), allowPending: true));

    [Fact]
    public async Task WhenNoTenantWasResolvedNothingPasses() =>
        Assert.False(await SucceedsAsync(null, Member("acme"), allowPending: true));

    [Fact]
    public async Task ASessionWithoutATenantClaimFails() =>
        Assert.False(await SucceedsAsync("acme", Member(null), allowPending: true));

    [Fact]
    public async Task APrincipalThatIsNotAPortalMemberFails() =>
        Assert.False(await SucceedsAsync("acme", Member("acme", withUserId: false), allowPending: true));

    [Fact]
    public async Task APendingPasswordChangeOnlyOpensThePagesThatAllowIt()
    {
        Assert.False(await SucceedsAsync("acme", Member("acme", passwordChangePending: true), allowPending: false));
        Assert.True(await SucceedsAsync("acme", Member("acme", passwordChangePending: true), allowPending: true));
    }
}