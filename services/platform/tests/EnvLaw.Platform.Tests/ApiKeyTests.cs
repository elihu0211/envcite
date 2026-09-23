using System.Net;
using System.Net.Http.Json;

using EnvLaw.Platform.Modules.ApiAccess;
using EnvLaw.Platform.Modules.Tenancy;

namespace EnvLaw.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class ApiKeyTests(PlatformFactory factory)
{
    [Fact]
    public async Task KeyResolvesItsOwnTenant()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("me");
        using var client = factory.CreateTenantClient(key.Key);

        var me = await client.GetFromJsonAsync<TenantResponse>("/v1/me");

        Assert.Equal(tenant.Identifier, me!.Identifier);
    }

    [Fact]
    public async Task TenantsCannotSeeOrRevokeEachOthersKeys()
    {
        var (_, keyA) = await factory.CreateTenantWithKeyAsync("iso-a");
        var (_, keyB) = await factory.CreateTenantWithKeyAsync("iso-b");
        using var clientA = factory.CreateTenantClient(keyA.Key);
        using var clientB = factory.CreateTenantClient(keyB.Key);

        var keysSeenByA = await clientA.GetFromJsonAsync<ApiKeySummary[]>("/v1/api-keys");
        using var revokeOther = await clientA.DeleteAsync(new Uri($"/v1/api-keys/{keyB.ApiKey.Id}", UriKind.Relative));
        using var bStillWorks = await clientB.GetAsync(new Uri("/v1/me", UriKind.Relative));

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

        using var revoke = await keeper.DeleteAsync(new Uri($"/v1/api-keys/{doomed.ApiKey.Id}", UriKind.Relative));
        using var afterRevoke = await doomedClient.GetAsync(new Uri("/v1/me", UriKind.Relative));

        Assert.Equal(HttpStatusCode.NoContent, revoke.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, afterRevoke.StatusCode);
    }

    [Fact]
    public async Task ExpiredKeyIsRejected()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("expiry", factory.Time.GetUtcNow().AddHours(1));
        using var client = factory.CreateTenantClient(key.Key);

        using var beforeExpiry = await client.GetAsync(new Uri("/v1/me", UriKind.Relative));
        factory.Time.Advance(TimeSpan.FromHours(2));
        using var afterExpiry = await client.GetAsync(new Uri("/v1/me", UriKind.Relative));

        Assert.Equal(HttpStatusCode.OK, beforeExpiry.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, afterExpiry.StatusCode);
    }

    [Theory]
    [InlineData("sk_live_000000000000_notarealsecret")]
    [InlineData("not-a-key")]
    public async Task UnknownOrMalformedKeyIsRejected(string apiKey)
    {
        using var client = factory.CreateTenantClient(apiKey);

        using var response = await client.GetAsync(new Uri("/v1/me", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task WrongSecretForExistingPrefixIsRejected()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("tamper");
        using var client = factory.CreateTenantClient(key.Key[..^4] + "AAAA");

        using var response = await client.GetAsync(new Uri("/v1/me", UriKind.Relative));

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
            using var response = await busyClient.GetAsync(new Uri("/v1/me", UriKind.Relative));
            statuses.Add(response.StatusCode);
        }

        using var quietResponse = await quietClient.GetAsync(new Uri("/v1/me", UriKind.Relative));

        Assert.All(statuses[..^1], s => Assert.Equal(HttpStatusCode.OK, s));
        Assert.Equal(HttpStatusCode.TooManyRequests, statuses[^1]);
        Assert.Equal(HttpStatusCode.OK, quietResponse.StatusCode);
    }
}