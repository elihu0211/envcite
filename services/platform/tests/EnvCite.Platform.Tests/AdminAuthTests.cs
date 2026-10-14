using System.Net;
using System.Net.Http.Json;

using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Tenancy;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class AdminAuthTests(PlatformFactory factory)
{
    private static readonly CreateTenantRequest Request = new("acme", "Acme");

    [Fact]
    public async Task AdminEndpointsRejectMissingKey()
    {
        using var client = factory.CreateClient();

        using var response = await client.PostAsJsonAsync("/admin/tenants", Request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task AdminEndpointsRejectWrongKey()
    {
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Add(AdminKeyAuthenticationHandler.HeaderName, "wrong");

        using var response = await client.PostAsJsonAsync("/admin/tenants", Request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task TenantApiKeyCannotCallAdminEndpoints()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("notadmin");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync("/admin/tenants", Request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task DuplicateTenantIdentifierConflicts()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("dup");
        using var admin = factory.CreateAdminClient();

        using var response = await admin.PostAsJsonAsync("/admin/tenants", new CreateTenantRequest(tenant.Identifier, "Again"));

        Assert.Equal(HttpStatusCode.Conflict, response.StatusCode);
    }
}