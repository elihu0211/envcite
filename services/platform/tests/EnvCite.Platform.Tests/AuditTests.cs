using System.Net;
using System.Text.Json;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group2)]
public sealed class AuditTests(PlatformFactory factory)
{
    private async Task<AuditPage> PageAsync(AuditFilter filter) =>
        await factory.Services.GetRequiredService<IAuditQuery>().QueryAsync(filter, CancellationToken.None);

    private async Task<List<AuditRecord>> ItemsAsync(string tenantId, string action) =>
        [.. (await PageAsync(new AuditFilter(Tenant: tenantId, Action: action))).Items];

    [Fact]
    public async Task CreatingATenantIsAuditedWithTheOperatorAsActor()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-t");

        var created = Assert.Single(await ItemsAsync(tenant.Id, AuditActions.TenantCreated));

        Assert.Equal(AuditActors.OperatorUser, created.ActorType);
        Assert.Equal(PlatformFactory.OperatorEmail, created.ActorId);
        Assert.Equal(tenant.Id, created.TenantId);
        Assert.Equal(tenant.Identifier, created.Detail.GetProperty("identifier").GetString());
    }

    [Fact]
    public async Task IssuingAKeyIsAuditedWithoutTheKeyOrItsHash()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("audit-k");

        var page = await PageAsync(new AuditFilter(Tenant: tenant.Id, Action: AuditActions.ApiKeyIssued));
        var body = JsonSerializer.Serialize(page);
        var issued = Assert.Single(page.Items);

        Assert.Equal(key.ApiKey.Id.ToString(), issued.TargetId);
        Assert.Equal(key.ApiKey.Prefix, issued.Detail.GetProperty("prefix").GetString());
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
        var revoked = Assert.Single(await ItemsAsync(tenant.Id, AuditActions.ApiKeyRevoked));
        Assert.Equal(AuditActors.TenantKey, revoked.ActorType);
        Assert.Equal(key.ApiKey.Id.ToString(), revoked.ActorId);
        Assert.Equal(other.ApiKey.Id.ToString(), revoked.TargetId);
    }

    [Fact]
    public async Task ARejectedRequestLeavesNoAuditEvent()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("audit-n");

        var bad = await factory.Services.GetRequiredService<IApiKeyAdmin>()
            .IssueAsync(OperatorSession.Principal(), tenant.Id, string.Empty, null, null, CancellationToken.None);

        Assert.Equal(AdminStatus.Invalid, bad.Status);
        Assert.Single(await ItemsAsync(tenant.Id, AuditActions.ApiKeyIssued)); // 只有建立租戶時簽的那一把
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

        var first = await PageAsync(new AuditFilter(Tenant: tenant.Id, Limit: 3));
        var firstIds = first.Items.Select(i => i.Id).ToList();
        var second = await PageAsync(new AuditFilter(Tenant: tenant.Id, Limit: 3, Before: first.NextCursor));
        var secondIds = second.Items.Select(i => i.Id).ToList();

        Assert.Equal(3, firstIds.Count);
        Assert.Equal(firstIds.OrderByDescending(i => i), firstIds);
        Assert.NotNull(first.NextCursor);
        Assert.Single(secondIds);
        Assert.True(secondIds[0] < firstIds[^1]);
        Assert.Null(second.NextCursor);
    }
}