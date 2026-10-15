using System.Net;
using System.Net.Http.Json;

using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Tenancy;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class ApiKeyTests(PlatformFactory factory)
{
    [Fact]
    public async Task KeyResolvesItsOwnTenant()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("me");
        using var client = factory.CreateTenantClient(key.Key);

        var me = await client.GetFromJsonAsync<TenantResponse>("/v2/me", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(tenant.Identifier, me!.Identifier);
    }

    [Fact]
    public async Task TenantsCannotSeeOrRevokeEachOthersKeys()
    {
        var (_, keyA) = await factory.CreateTenantWithKeyAsync("iso-a");
        var (_, keyB) = await factory.CreateTenantWithKeyAsync("iso-b");
        using var clientA = factory.CreateTenantClient(keyA.Key);
        using var clientB = factory.CreateTenantClient(keyB.Key);

        var keysSeenByA = await clientA.GetFromJsonAsync<ApiKeySummary[]>("/v2/api-keys", cancellationToken: TestContext.Current.CancellationToken);
        using var revokeOther = await clientA.DeleteAsync(new Uri($"/v2/api-keys/{keyB.ApiKey.Id}", UriKind.Relative), TestContext.Current.CancellationToken);
        using var bStillWorks = await clientB.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal([keyA.ApiKey.Id], keysSeenByA!.Select(k => k.Id));
        Assert.Equal(HttpStatusCode.NotFound, revokeOther.StatusCode);
        Assert.Equal(HttpStatusCode.OK, bStillWorks.StatusCode);
    }

    [Fact]
    public async Task RevokedKeyIsRejected()
    {
        var (tenant, keep) = await factory.CreateTenantWithKeyAsync("revoke");
        var doomed = await factory.IssueKeyAsync(tenant.Id);
        using var keeper = factory.CreateTenantClient(keep.Key);
        using var doomedClient = factory.CreateTenantClient(doomed.Key);

        using var revoke = await keeper.DeleteAsync(new Uri($"/v2/api-keys/{doomed.ApiKey.Id}", UriKind.Relative), TestContext.Current.CancellationToken);
        using var afterRevoke = await doomedClient.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.NoContent, revoke.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, afterRevoke.StatusCode);
    }

    [Fact]
    public async Task ExpiredKeyIsRejected()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("expiry", factory.Time.GetUtcNow().AddHours(1));
        using var client = factory.CreateTenantClient(key.Key);

        using var beforeExpiry = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
        factory.Time.Advance(TimeSpan.FromHours(2));
        using var afterExpiry = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, beforeExpiry.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, afterExpiry.StatusCode);
    }

    [Theory]
    [InlineData("sk_live_000000000000_notarealsecret")]
    [InlineData("not-a-key")]
    public async Task UnknownOrMalformedKeyIsRejected(string apiKey)
    {
        using var client = factory.CreateTenantClient(apiKey);

        using var response = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task WrongSecretForExistingPrefixIsRejected()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("tamper");
        using var client = factory.CreateTenantClient(key.Key[..^4] + "AAAA");

        using var response = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task RateLimitIsPerTenant()
    {
        var (_, busy) = await factory.CreateTenantWithKeyAsync("busy");
        var (_, quiet) = await factory.CreateTenantWithKeyAsync("quiet");
        using var busyClient = factory.CreateTenantClient(busy.Key);
        using var quietClient = factory.CreateTenantClient(quiet.Key);

        var statuses = new List<HttpStatusCode>();
        for (var i = 0; i <= PlatformFactory.RateLimitPermits; i++)
        {
            using var response = await busyClient.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
            statuses.Add(response.StatusCode);
        }

        using var quietResponse = await quietClient.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.All(statuses[..^1], s => Assert.Equal(HttpStatusCode.OK, s));
        Assert.Equal(HttpStatusCode.TooManyRequests, statuses[^1]);
        Assert.Equal(HttpStatusCode.OK, quietResponse.StatusCode);
    }

    [Fact]
    public async Task RateLimitFollowsTheTenantsPlan()
    {
        // The exact identifier, not CreateTenantWithKeyAsync's randomised one: the plan is assigned by it.
        using var admin = factory.CreateAdminClient();
        using var created = await admin.PostAsJsonAsync("/admin/tenants", new CreateTenantRequest(PlatformFactory.ProTenant, "Paying"), cancellationToken: TestContext.Current.CancellationToken);
        created.EnsureSuccessStatusCode();
        var tenant = (await created.Content.ReadFromJsonAsync<TenantResponse>(cancellationToken: TestContext.Current.CancellationToken))!;
        var paying = await factory.IssueKeyAsync(tenant.Id);
        using var client = factory.CreateTenantClient(paying.Key);

        var statuses = new List<HttpStatusCode>();
        for (var i = 0; i <= PlatformFactory.RateLimitPermits; i++)
        {
            using var response = await client.GetAsync(new Uri("/v2/me", UriKind.Relative), TestContext.Current.CancellationToken);
            statuses.Add(response.StatusCode);
        }

        // One past the standard allowance is still within pro's.
        Assert.All(statuses, s => Assert.Equal(HttpStatusCode.OK, s));
    }
}