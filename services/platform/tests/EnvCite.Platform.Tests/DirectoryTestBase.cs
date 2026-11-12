using System.Security.Claims;

using EnvCite.Platform.Api;
using EnvCite.Platform.Data;
using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>目錄同步測試共用：一個租戶、一位擁有者、已開啟的連線與一把 token，以及在租戶內執行服務的方式。</summary>
public abstract class DirectoryTestBase(PlatformFactory factory)
{
    protected PlatformFactory Factory { get; } = factory;

    protected sealed record Org(TenantResponse Tenant, string OwnerId, ClaimsPrincipal Owner, ClaimsPrincipal Idp, Guid TokenId, string Token);

    protected static CancellationToken Ct => TestContext.Current.CancellationToken;

    protected static ClaimsPrincipal Portal(string userId) => new(new ClaimsIdentity([new Claim(PlatformClaimTypes.PortalUserId, userId)], "portal"));

    protected static ClaimsPrincipal Idp(Guid tokenId) => new(new ClaimsIdentity([new Claim(PlatformClaimTypes.DirectoryTokenId, tokenId.ToString())], "directory"));

    /// <summary>租戶 context 是 AsyncLocal，必須在同一個 async 流程裡綁定、解析、使用，所以整段交給 <paramref name="body"/>。</summary>
    protected async Task<T> InTenantAsync<T>(TenantResponse tenant, Func<IServiceProvider, Task<T>> body, WebApplicationFactory<Program>? host = null)
    {
        await using var scope = (host ?? Factory).Services.CreateAsyncScope();
        var info = (await scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>().GetAsync(tenant.Id))!;
        Bind(scope, info);
        return await body(scope.ServiceProvider);
    }

    protected static void Bind(AsyncServiceScope scope, TenantInfo info) =>
        scope.ServiceProvider.GetRequiredService<IMultiTenantContextSetter>().MultiTenantContext = new MultiTenantContext<TenantInfo>(info);

    /// <summary>一個租戶、一位擁有者、已開啟的連線與一把 token。</summary>
    protected async Task<Org> OrgAsync(string prefix, bool adopt = false, string defaultRole = CustomerRoles.Viewer, bool enabled = true)
    {
        var (tenant, _) = await Factory.CreateTenantWithKeyAsync(prefix);
        var (ownerId, _) = await AddMemberAsync(Factory, tenant.Id, NewEmail(), CustomerRoles.Owner);
        var owner = Portal(ownerId);

        var configured = await InTenantAsync(tenant, sp => sp.GetRequiredService<IDirectorySelfService>()
            .ConfigureAsync(owner, new DirectorySettings(enabled, defaultRole, adopt), Ct));
        Assert.True(configured.IsOk, configured.Message);

        var issued = await InTenantAsync(tenant, sp => sp.GetRequiredService<IDirectorySelfService>().IssueTokenAsync(owner, "idp", null, Ct));
        Assert.True(issued.IsOk, issued.Message);
        return new Org(tenant, ownerId, owner, Idp(issued.Value!.Info.Id), issued.Value.Info.Id, issued.Value.Token);
    }

    protected Task<ApplicationUser> UserAsync(Org org, string id) =>
        InTenantAsync(org.Tenant, async sp => (await sp.GetRequiredService<UserManager<ApplicationUser>>().FindByIdAsync(id))!);

    protected async Task<IReadOnlyList<AuditRecord>> AuditAsync(Org org, string action) =>
        (await Factory.Services.GetRequiredService<IAuditQuery>().QueryAsync(new AuditFilter(Tenant: org.Tenant.Id, Action: action, Limit: 200), Ct)).Items;

    protected static DirectoryProvision Person(string? externalId = null, string? role = null, bool active = true, string? email = null) =>
        new(email ?? NewEmail(), "Ann", externalId ?? $"ext-{Guid.NewGuid():N}", role, active);
}