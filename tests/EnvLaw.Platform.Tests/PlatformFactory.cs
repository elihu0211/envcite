using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;

using EnvLaw.Platform.Modules.ApiAccess;
using EnvLaw.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Time.Testing;

using Testcontainers.PostgreSql;

namespace EnvLaw.Platform.Tests;

/// <summary>Boots the API against a throwaway PostgreSQL container with a controllable clock.</summary>
public sealed class PlatformFactory : WebApplicationFactory<Program>, IAsyncLifetime
{
    public const string AdminKey = "test-admin-key";
    public const int RateLimitPermits = 5;

    private readonly PostgreSqlContainer _postgres = new PostgreSqlBuilder("postgres:17-alpine").Build();

    public FakeTimeProvider Time { get; } = new(DateTimeOffset.UtcNow);

    public async Task InitializeAsync() => await _postgres.StartAsync();

    async Task IAsyncLifetime.DisposeAsync()
    {
        await DisposeAsync();
        await _postgres.DisposeAsync();
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("ConnectionStrings:Platform", _postgres.GetConnectionString());
        builder.UseSetting("Platform:ApplyMigrationsOnStartup", "true");
        builder.UseSetting("Platform:AdminKeySha256",
            Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(AdminKey))));
        builder.UseSetting("Platform:RateLimit:PermitLimit", RateLimitPermits.ToString(System.Globalization.CultureInfo.InvariantCulture));
        builder.ConfigureServices(services => services.Replace(ServiceDescriptor.Singleton<TimeProvider>(Time)));
    }

    public HttpClient CreateAdminClient()
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Add(AdminKeyAuthenticationHandler.HeaderName, AdminKey);
        return client;
    }

    public HttpClient CreateTenantClient(string apiKey)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        return client;
    }

    /// <summary>Creates a tenant with a unique identifier and issues one API key for it.</summary>
    public async Task<(TenantResponse Tenant, CreatedApiKey Key)> CreateTenantWithKeyAsync(
        string prefix, DateTimeOffset? expiresAt = null)
    {
        using var admin = CreateAdminClient();
        var identifier = $"{prefix}-{Guid.NewGuid():N}"[..30];

        using var tenantResponse = await admin.PostAsJsonAsync("/admin/tenants", new CreateTenantRequest(identifier, prefix));
        tenantResponse.EnsureSuccessStatusCode();
        var tenant = (await tenantResponse.Content.ReadFromJsonAsync<TenantResponse>())!;

        var key = await IssueKeyAsync(tenant.Id, expiresAt);
        return (tenant, key);
    }

    public async Task<CreatedApiKey> IssueKeyAsync(string tenantId, DateTimeOffset? expiresAt = null)
    {
        using var admin = CreateAdminClient();
        using var response = await admin.PostAsJsonAsync(
            $"/admin/tenants/{tenantId}/api-keys", new CreateApiKeyRequest("test", ["ontology:read"], expiresAt));
        response.EnsureSuccessStatusCode();
        return (await response.Content.ReadFromJsonAsync<CreatedApiKey>())!;
    }
}

[CollectionDefinition(Name)]
public sealed class PlatformTestGroup : ICollectionFixture<PlatformFactory>
{
    public const string Name = "Platform";
}