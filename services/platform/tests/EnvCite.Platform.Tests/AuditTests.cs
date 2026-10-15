using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class AuditTests(PlatformFactory factory)
{
    private async Task<JsonElement> EventsAsync(string query)
    {
        using var admin = factory.CreateAdminClient();
        using var response = await admin.GetAsync(new Uri($"/admin/audit-events?{query}", UriKind.Relative));
        response.EnsureSuccessStatusCode();
        return (await response.Content.ReadFromJsonAsync<JsonElement>()).Clone();
    }

    private async Task<List<JsonElement>> ItemsAsync(string query) =>
        (await EventsAsync(query)).GetProperty("items").EnumerateArray().ToList();

    [Fact]
    public async Task CreatingATenantIsAuditedWithTheOperatorKeyAsActor()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-t");

        var created = (await ItemsAsync($"tenant={tenant.Id}&action=tenant.created")).Single();

        Assert.Equal("operator-key", created.GetProperty("actorType").GetString());
        Assert.Equal("admin-key", created.GetProperty("actorId").GetString());
        Assert.Equal(tenant.Id, created.GetProperty("tenantId").GetString());
        Assert.Equal(tenant.Identifier, created.GetProperty("detail").GetProperty("identifier").GetString());
    }

    [Fact]
    public async Task IssuingAKeyIsAuditedWithoutTheKeyOrItsHash()
    {
        using var admin = factory.CreateAdminClient();
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("audit-k");

        using var response = await admin.GetAsync(new Uri($"/admin/audit-events?tenant={tenant.Id}&action=api_key.issued", UriKind.Relative), TestContext.Current.CancellationToken);
        var body = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        var issued = JsonDocument.Parse(body).RootElement.GetProperty("items").EnumerateArray().Single();

        Assert.Equal(key.ApiKey.Id.ToString(), issued.GetProperty("targetId").GetString());
        Assert.Equal(key.ApiKey.Prefix, issued.GetProperty("detail").GetProperty("prefix").GetString());
        Assert.DoesNotContain(key.Key, body, StringComparison.Ordinal);
        Assert.DoesNotContain("hash", body, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task RevokingAKeyIsAuditedOnceEvenWhenRepeated()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("audit-r");
        using var tenantClient = factory.CreateTenantClient(key.Key);
        var other = await factory.IssueKeyAsync(tenant.Id); // 撤銷的是另一把，這把還要用來呼叫

        using var first = await tenantClient.DeleteAsync(new Uri($"/v2/api-keys/{other.ApiKey.Id}", UriKind.Relative), TestContext.Current.CancellationToken);
        using var again = await tenantClient.DeleteAsync(new Uri($"/v2/api-keys/{other.ApiKey.Id}", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.NoContent, first.StatusCode);
        Assert.Equal(HttpStatusCode.NoContent, again.StatusCode);
        var revoked = (await ItemsAsync($"tenant={tenant.Id}&action=api_key.revoked")).Single();
        Assert.Equal("tenant-key", revoked.GetProperty("actorType").GetString());
        Assert.Equal(key.ApiKey.Id.ToString(), revoked.GetProperty("actorId").GetString());
        Assert.Equal(other.ApiKey.Id.ToString(), revoked.GetProperty("targetId").GetString());
    }

    [Fact]
    public async Task ARejectedRequestLeavesNoAuditEvent()
    {
        using var admin = factory.CreateAdminClient();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-n");

        using var bad = await admin.PostAsJsonAsync($"/admin/tenants/{tenant.Id}/api-keys", new { name = "" }, cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, bad.StatusCode);
        Assert.Single(await ItemsAsync($"tenant={tenant.Id}&action=api_key.issued")); // 只有建立租戶時簽的那一把
    }

    // 只能新增由資料庫強制，不是程式自律：連直接下 SQL 也改不動、刪不掉、清不空。
    [Theory]
    [InlineData("UPDATE platform.\"AuditEvents\" SET \"Action\" = 'tampered' WHERE true")]
    [InlineData("DELETE FROM platform.\"AuditEvents\" WHERE true")]
    [InlineData("TRUNCATE platform.\"AuditEvents\"")]
    public async Task TheDatabaseRefusesToChangeOrRemoveAuditEvents(string sql)
    {
        _ = await factory.CreateTenantWithKeyAsync("audit-a"); // 確保至少有一列
        await using var scope = factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();

        var failure = await Assert.ThrowsAnyAsync<Exception>(() => db.Database.ExecuteSqlRawAsync(sql, cancellationToken: TestContext.Current.CancellationToken));

        Assert.Contains("append-only", failure.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task EventsPageNewestFirstWithACursor()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-p");
        await factory.IssueKeyAsync(tenant.Id);
        await factory.IssueKeyAsync(tenant.Id); // 該租戶共 4 筆：建立、簽發 ×3

        var first = await EventsAsync($"tenant={tenant.Id}&limit=3");
        var firstIds = first.GetProperty("items").EnumerateArray().Select(i => i.GetProperty("id").GetGuid()).ToList();
        var cursor = first.GetProperty("nextCursor").GetGuid();
        var second = await EventsAsync($"tenant={tenant.Id}&limit=3&before={cursor}");
        var secondIds = second.GetProperty("items").EnumerateArray().Select(i => i.GetProperty("id").GetGuid()).ToList();

        Assert.Equal(3, firstIds.Count);
        Assert.Equal(firstIds.OrderByDescending(i => i), firstIds);
        Assert.Single(secondIds);
        Assert.True(secondIds[0] < firstIds[^1]);
        Assert.Equal(JsonValueKind.Null, second.GetProperty("nextCursor").ValueKind);
    }

    [Fact]
    public async Task OnlyOperatorsCanReadTheAuditLog()
    {
        using var anonymous = factory.CreateClient();
        var (_, key) = await factory.CreateTenantWithKeyAsync("audit-x");
        using var tenant = factory.CreateTenantClient(key.Key);

        using var none = await anonymous.GetAsync(new Uri("/admin/audit-events", UriKind.Relative), TestContext.Current.CancellationToken);
        using var asTenant = await tenant.GetAsync(new Uri("/admin/audit-events", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, none.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, asTenant.StatusCode);
    }
}