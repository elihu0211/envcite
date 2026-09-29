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

    /// <summary>Tenant identifier assigned the <c>pro</c> plan, which allows twice <see cref="RateLimitPermits"/>.</summary>
    public const string ProTenant = "paying";

    private readonly PostgreSqlContainer _postgres = new PostgreSqlBuilder("postgres:18").Build();

    public FakeTimeProvider Time { get; } = new(DateTimeOffset.UtcNow);

    public FakeOntologyServer Ontology { get; } = new();

    public FakeConsultServer Consult { get; } = new();

    public async Task InitializeAsync()
    {
        await Task.WhenAll(_postgres.StartAsync(), Ontology.StartAsync(), Consult.StartAsync());
    }

    async Task IAsyncLifetime.DisposeAsync()
    {
        await DisposeAsync();
        await Ontology.DisposeAsync();
        await Consult.DisposeAsync();
        await _postgres.DisposeAsync();
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("ConnectionStrings:Platform", _postgres.GetConnectionString());
        builder.UseSetting("Platform:ApplyMigrationsOnStartup", "true");
        builder.UseSetting("Platform:AdminKeySha256",
            Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(AdminKey))));
        builder.UseSetting("Ontology:Address", Ontology.Address.ToString());
        builder.UseSetting("Ontology:InternalToken", FakeOntologyServer.InternalToken);
        builder.UseSetting("Consult:Address", Consult.Address.ToString());
        builder.UseSetting("Consult:InternalToken", FakeConsultServer.InternalToken);
        builder.UseSetting("Platform:Plans:free:PermitLimit", RateLimitPermits.ToString(System.Globalization.CultureInfo.InvariantCulture));
        builder.UseSetting("Platform:Plans:pro:PermitLimit", (RateLimitPermits * 2).ToString(System.Globalization.CultureInfo.InvariantCulture));
        builder.UseSetting($"Platform:TenantPlans:{ProTenant}", "pro");
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