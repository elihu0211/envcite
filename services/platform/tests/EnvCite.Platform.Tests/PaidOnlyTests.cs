using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;

using EnvCite.Platform.Modules.ApiAccess;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 完全付費制：沒有方案的租戶不能呼叫資料端點（402），但仍能管理自己的帳號。
/// 測試用的主機設了預設方案（為了動態建立的租戶），這裡的主機把它清掉，才是正式環境的樣子。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class PaidOnlyTests(PlatformFactory factory)
{
    private static readonly Uri LawUri = new($"{FakeOntologyServer.CentralLawsPath}{FakeOntologyServer.KnownLawId}", UriKind.Relative);

    private WebApplicationFactory<Program> HostWithoutDefaultPlan(Action<IWebHostBuilder>? more = null) =>
        factory.WithWebHostBuilder(b =>
        {
            b.UseSetting("Platform:DefaultPlan", string.Empty);
            more?.Invoke(b);
        });

    private static HttpClient ClientFor(WebApplicationFactory<Program> host, string apiKey)
    {
        var client = host.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        return client;
    }

    [Fact]
    public async Task ATenantWithoutAPlanGets402OnDataEndpoints()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("unplanned");
        await using var host = HostWithoutDefaultPlan();
        using var client = ClientFor(host, key.Key);

        using var law = await client.GetAsync(LawUri, TestContext.Current.CancellationToken);
        using var ground = await client.PostAsJsonAsync("/v2/ground", new { question = "x" }, cancellationToken: TestContext.Current.CancellationToken);
        using var reasoning = await client.PostAsJsonAsync("/v2/reasoning/evaluate", new { conductOn = "2020-01-01", decidedOn = "2020-06-01" }, cancellationToken: TestContext.Current.CancellationToken);

        foreach (var response in new[] { law, ground, reasoning })
        {
            Assert.Equal(HttpStatusCode.PaymentRequired, response.StatusCode);
            Assert.Equal("application/problem+json", response.Content.Headers.ContentType?.MediaType);
            var problem = await response.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken);
            Assert.Equal(402, problem.GetProperty("status").GetInt32());
        }
    }

    [Fact]
    public async Task ATenantWithoutAPlanCanStillManageItsAccount()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("unplanned-mgmt");
        var spare = await factory.IssueKeyAsync(tenant.Id);
        await using var host = HostWithoutDefaultPlan();
        using var client = ClientFor(host, key.Key);

        using var me = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
        using var usage = await client.GetAsync(new Uri("/v2/usage", UriKind.Relative), TestContext.Current.CancellationToken);
        using var keys = await client.GetAsync(new Uri("/v2/api-keys", UriKind.Relative), TestContext.Current.CancellationToken);
        // 被盜的 key 要能撤銷，不管有沒有方案。
        using var revoke = await client.DeleteAsync(new Uri($"/v2/api-keys/{spare.ApiKey.Id}", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, me.StatusCode);
        Assert.Equal(HttpStatusCode.OK, usage.StatusCode);
        Assert.Equal(HttpStatusCode.OK, keys.StatusCode);
        Assert.Equal(HttpStatusCode.NoContent, revoke.StatusCode);
    }

    [Fact]
    public async Task AMissingKeyIsStill401NotPaymentRequired()
    {
        await using var host = HostWithoutDefaultPlan();
        using var client = host.CreateClient();

        using var response = await client.GetAsync(LawUri, TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // 方案指派之後同一個租戶就有服務：付費制不是把服務關掉，是把「有沒有方案」變成明確的開關。
    [Fact]
    public async Task AssigningAPlanTurnsTheDataEndpointsOn()
    {
        var identifier = $"paid-{Guid.NewGuid():N}"[..20];
        var tenant = await factory.CreateTenantAsync(identifier, "Paid");
        var key = await factory.IssueKeyAsync(tenant.Id);

        await using (var without = HostWithoutDefaultPlan())
        {
            using var before = await ClientFor(without, key.Key).GetAsync(LawUri, TestContext.Current.CancellationToken);
            Assert.Equal(HttpStatusCode.PaymentRequired, before.StatusCode);
        }

        await using var withPlan = HostWithoutDefaultPlan(b => b.UseSetting($"Platform:TenantPlans:{identifier}", "standard"));
        using var after = await ClientFor(withPlan, key.Key).GetAsync(LawUri, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, after.StatusCode);
    }

    // 沒有方案的租戶只能走管理端點，並受限流底線約束，免得被拿來灌流量。
    [Fact]
    public async Task AnUnplannedTenantIsHeldToTheRateFloorOnManagementEndpoints()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("floor");
        await using var host = HostWithoutDefaultPlan();
        using var client = ClientFor(host, key.Key);

        for (var i = 0; i < PlanCatalog.UnplannedFloor.PermitLimit; i++)
        {
            using var ok = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
            Assert.Equal(HttpStatusCode.OK, ok.StatusCode);
        }

        using var over = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.TooManyRequests, over.StatusCode);
    }
}