using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;

using EnvCite.Platform.Data;
using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 方案指派在資料庫、不在設定：營運者改了立即生效、留稽核，不必重新部署。
/// 這裡的主機都清掉預設方案（正式環境的樣子）；每個主機有自己的記憶體快照，共用同一個資料庫，
/// 所以兩個主機就是兩個副本。營運者的動作直接呼叫服務（營運後台用的就是它們）。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class EntitlementTests(PlatformFactory factory)
{
    private static readonly Uri LawUri = new($"{FakeOntologyServer.CentralLawsPath}{FakeOntologyServer.KnownLawId}", UriKind.Relative);

    private WebApplicationFactory<Program> Replica() =>
        factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));

    private static IPlanAdmin Plans(WebApplicationFactory<Program> host) => host.Services.GetRequiredService<IPlanAdmin>();

    private static IApiKeyAdmin Keys(WebApplicationFactory<Program> host) => host.Services.GetRequiredService<IApiKeyAdmin>();

    private static HttpClient Tenant(WebApplicationFactory<Program> host, string apiKey)
    {
        var client = host.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        return client;
    }

    private static async Task<List<AuditRecord>> EventsAsync(WebApplicationFactory<Program> host, string tenantId, string action) =>
        [.. (await host.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenantId, Action: action), CancellationToken.None)).Items];

    [Fact]
    public async Task AssigningAPlanTurnsTheDataEndpointsOnImmediatelyAndRemovingItTurnsThemOff()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ent-a");
        await using var host = Replica();
        using var client = Tenant(host, key.Key);

        using (var before = await client.GetAsync(LawUri, TestContext.Current.CancellationToken))
        {
            Assert.Equal(HttpStatusCode.PaymentRequired, before.StatusCode);
        }

        Assert.True((await Plans(host).SetAsync(OperatorSession.Principal(), tenant.Id, "standard", CancellationToken.None)).IsOk);
        using (var after = await client.GetAsync(LawUri, TestContext.Current.CancellationToken))
        {
            Assert.Equal(HttpStatusCode.OK, after.StatusCode);
        }

        Assert.True((await Plans(host).SetAsync(OperatorSession.Principal(), tenant.Id, null, CancellationToken.None)).IsOk);
        using var removed = await client.GetAsync(LawUri, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.PaymentRequired, removed.StatusCode);
    }

    [Fact]
    public async Task EveryChangeIsAuditedOnceWithWhatItWasAndWhatItBecame()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ent-b");
        await using var host = Replica();
        var actor = OperatorSession.Principal();

        await Plans(host).SetAsync(actor, tenant.Id, "standard", CancellationToken.None);
        await Plans(host).SetAsync(actor, tenant.Id, "standard", CancellationToken.None); // 冪等：不再留紀錄
        await Plans(host).SetAsync(actor, tenant.Id, "pro", CancellationToken.None);
        await Plans(host).SetAsync(actor, tenant.Id, null, CancellationToken.None);

        var assigned = await EventsAsync(host, tenant.Id, AuditActions.TenantPlanAssigned);
        var removed = await EventsAsync(host, tenant.Id, AuditActions.TenantPlanRemoved);

        Assert.Equal(2, assigned.Count); // 無 → standard、standard → pro；重複那次沒有
        var newest = assigned[0]; // 新到舊
        Assert.Equal("standard", newest.Detail.GetProperty("previous").GetString());
        Assert.Equal("pro", newest.Detail.GetProperty("plan").GetString());
        Assert.Equal(AuditActors.OperatorUser, newest.ActorType);
        Assert.Equal(JsonValueKind.Null, assigned[1].Detail.GetProperty("previous").ValueKind);
        Assert.Single(removed);
        Assert.Equal("pro", removed[0].Detail.GetProperty("previous").GetString());
    }

    [Fact]
    public async Task BadInputIsRefusedWithoutTouchingAnything()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ent-c");
        await using var host = Replica();
        var actor = OperatorSession.Principal();

        var unknownPlan = await Plans(host).SetAsync(actor, tenant.Id, "gold", CancellationToken.None);
        var noTenant = await Plans(host).SetAsync(actor, "no-such-tenant", "standard", CancellationToken.None);

        Assert.Equal(AdminStatus.Invalid, unknownPlan.Status);
        Assert.Equal(OutcomeCodes.PlanUnknown, unknownPlan.Code);
        Assert.Equal(AdminStatus.NotFound, noTenant.Status);
        Assert.Empty(await EventsAsync(host, tenant.Id, AuditActions.TenantPlanAssigned));
    }

    // 兩個主機 = 兩個副本：營運者在 A 改，B 在重新載入之前還看不到，重新載入之後看得到。
    [Fact]
    public async Task AnotherReplicaSeesTheChangeAfterItsNextRefresh()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ent-d");
        await using var a = Replica();
        await using var b = Replica();
        using var clientB = Tenant(b, key.Key);

        Assert.True((await Plans(a).SetAsync(OperatorSession.Principal(), tenant.Id, "standard", CancellationToken.None)).IsOk);

        using (var stale = await clientB.GetAsync(LawUri, TestContext.Current.CancellationToken))
        {
            Assert.Equal(HttpStatusCode.PaymentRequired, stale.StatusCode);
        }

        await b.Services.GetRequiredService<IEntitlementRefresher>().RefreshAsync(CancellationToken.None);
        using var fresh = await clientB.GetAsync(LawUri, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, fresh.StatusCode);
    }

    // 限流額度跟著方案走，換方案立即生效（分區鍵含方案名稱，換方案就是新的限流器）。
    [Fact]
    public async Task ChangingThePlanChangesTheRateLimitAtOnce()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ent-e");
        await using var host = Replica();
        using var client = Tenant(host, key.Key);
        Assert.True((await Plans(host).SetAsync(OperatorSession.Principal(), tenant.Id, "standard", CancellationToken.None)).IsOk);

        for (var i = 0; i < PlatformFactory.RateLimitPermits; i++)
        {
            using var ok = await client.GetAsync(LawUri, TestContext.Current.CancellationToken);
            Assert.Equal(HttpStatusCode.OK, ok.StatusCode);
        }

        using (var over = await client.GetAsync(LawUri, TestContext.Current.CancellationToken))
        {
            Assert.Equal(HttpStatusCode.TooManyRequests, over.StatusCode);
        }

        Assert.True((await Plans(host).SetAsync(OperatorSession.Principal(), tenant.Id, "pro", CancellationToken.None)).IsOk);
        using var upgraded = await client.GetAsync(LawUri, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, upgraded.StatusCode);
    }

    // 資料庫裡的指派指向後來從設定移除的方案：當成沒有方案（402），不是讓每個請求都 500。
    [Fact]
    public async Task AnAssignmentToAPlanThatNoLongerExistsFailsClosed()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ent-f");
        await using (var scope = factory.Services.CreateAsyncScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
            db.Add(new TenantEntitlement { TenantId = tenant.Id, TenantIdentifier = tenant.Identifier, Plan = "ghost", UpdatedAt = DateTimeOffset.UtcNow });
            await db.SaveChangesAsync(TestContext.Current.CancellationToken);
        }

        await using var host = Replica();
        await host.Services.GetRequiredService<IEntitlementRefresher>().RefreshAsync(CancellationToken.None);
        using var client = Tenant(host, key.Key);

        using var response = await client.GetAsync(LawUri, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.PaymentRequired, response.StatusCode);
    }

    [Fact]
    public async Task ThePlanListShowsWhatIsDefinedInConfiguration()
    {
        await using var host = Replica();

        var names = Plans(host).Plans.Select(p => p.Name).ToList();

        Assert.Contains("standard", names);
        Assert.Contains("pro", names);
    }

    // ---- API key 管理 --------------------------------------------------------------------------

    [Fact]
    public async Task AnOperatorCanListAndRevokeATenantsKeysAndItIsAudited()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("ent-g");
        var second = await factory.IssueKeyAsync(tenant.Id);
        await using var host = Replica();
        var actor = OperatorSession.Principal();

        var list = await Keys(host).ListAsync(tenant.Id, CancellationToken.None);
        var revoke = await Keys(host).RevokeAsync(actor, tenant.Id, second.ApiKey.Id, CancellationToken.None);
        var again = await Keys(host).RevokeAsync(actor, tenant.Id, second.ApiKey.Id, CancellationToken.None);
        using var revokedKeyCall = await Tenant(host, second.Key).GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
        using var otherKeyCall = await Tenant(host, key.Key).GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(2, list.Value!.Count);
        Assert.True(revoke.IsOk);
        Assert.True(again.IsOk);
        Assert.Equal(revoke.Value!.RevokedAt, again.Value!.RevokedAt); // 重複撤銷不改變原撤銷時間
        Assert.Equal(HttpStatusCode.Unauthorized, revokedKeyCall.StatusCode);
        Assert.Equal(HttpStatusCode.OK, otherKeyCall.StatusCode);
        var revoked = await EventsAsync(host, tenant.Id, AuditActions.ApiKeyRevoked);
        var event_ = Assert.Single(revoked); // 重複撤銷不重複記錄
        Assert.Equal(AuditActors.OperatorUser, event_.ActorType);
        Assert.Equal(second.ApiKey.Id.ToString(), event_.TargetId);
    }

    // 不能拿 A 租戶的 id 去撤銷 B 租戶的 key。
    [Fact]
    public async Task AKeyCannotBeRevokedThroughAnotherTenant()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("ent-h1");
        var (b, keyOfB) = await factory.CreateTenantWithKeyAsync("ent-h2");
        await using var host = Replica();

        var response = await Keys(host).RevokeAsync(OperatorSession.Principal(), a.Id, keyOfB.ApiKey.Id, CancellationToken.None);
        using var stillWorks = await Tenant(host, keyOfB.Key).GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(AdminStatus.NotFound, response.Status);
        Assert.Equal(HttpStatusCode.OK, stillWorks.StatusCode);
        Assert.Empty(await EventsAsync(host, b.Id, AuditActions.ApiKeyRevoked));
    }

    [Fact]
    public async Task ListingTheKeysOfAMissingTenantIsNotFound()
    {
        await using var host = Replica();

        var response = await Keys(host).ListAsync("no-such-tenant", CancellationToken.None);

        Assert.Equal(AdminStatus.NotFound, response.Status);
    }
}