using System.Net;
using System.Text.RegularExpressions;

using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 營運後台的動作：建立租戶、簽發／撤銷 key、指派方案。動作的邏輯在共用服務裡（<c>/admin</c> 端點用同一份），
/// 所以這裡直接測服務，並確認「誰做的」來自已登入的營運者；頁面測的是伺服器端渲染出的內容與語意。
/// 對話框的點擊流程沒有自動化測試（這個專案沒有瀏覽器測試），改以手動在瀏覽器驗證。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
public sealed class OperatorActionsTests(PlatformFactory factory)
{
    private static string Unique(string prefix) => $"{prefix}-{Guid.NewGuid().ToString("N")[..8]}";

    private static async Task<List<AuditRecord>> EventsAsync(WebApplicationFactory<Program> host, string tenantId, string action) =>
        [.. (await host.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenantId, Action: action), CancellationToken.None)).Items];

    private WebApplicationFactory<Program> NoDefaultPlan() =>
        factory.WithWebHostBuilder(b => b.UseSetting("Platform:DefaultPlan", string.Empty));

    [Fact]
    public async Task CreatingATenantIsAuditedWithTheSignedInOperator()
    {
        var provisioner = factory.Services.GetRequiredService<ITenantProvisioner>();
        var identifier = Unique("created");

        var created = await provisioner.CreateAsync(OperatorSession.Principal(), identifier, "  某某顧問  ", CancellationToken.None);

        Assert.True(created.IsOk);
        Assert.Equal("某某顧問", created.Value!.Name); // 名稱去除前後空白
        var events = await EventsAsync(factory, created.Value.Id, AuditActions.TenantCreated);
        var only = Assert.Single(events);
        Assert.Equal(AuditActors.OperatorUser, only.ActorType);
        Assert.Equal(PlatformFactory.OperatorEmail, only.ActorId);
    }

    [Theory]
    [InlineData("", "名稱")]
    [InlineData("ab", "名稱")] // 太短
    [InlineData("Has-Upper", "名稱")]
    [InlineData("-leading", "名稱")]
    [InlineData("trailing-", "名稱")]
    [InlineData("under_score", "名稱")]
    [InlineData("valid-id", "")] // 名稱空白
    [InlineData("valid-id", "   ")]
    public async Task InvalidInputIsRejectedWithAReasonAndLeavesNothingBehind(string identifier, string name)
    {
        var provisioner = factory.Services.GetRequiredService<ITenantProvisioner>();

        var created = await provisioner.CreateAsync(OperatorSession.Principal(), identifier, name, CancellationToken.None);

        Assert.Equal(AdminStatus.Invalid, created.Status);
        Assert.False(string.IsNullOrWhiteSpace(created.Message));
    }

    [Fact]
    public async Task ADuplicateIdentifierIsAConflictNotASecondTenant()
    {
        var provisioner = factory.Services.GetRequiredService<ITenantProvisioner>();
        var identifier = Unique("dup");
        var first = await provisioner.CreateAsync(OperatorSession.Principal(), identifier, "First", CancellationToken.None);
        Assert.True(first.IsOk);

        var again = await provisioner.CreateAsync(OperatorSession.Principal(), identifier, "Second", CancellationToken.None);

        Assert.Equal(AdminStatus.Conflict, again.Status);
        var tenants = await factory.Services.GetRequiredService<ITenantDirectory>().ListAsync(CancellationToken.None);
        Assert.Single(tenants, t => t.Identifier == identifier);
    }

    [Fact]
    public async Task KeyAndPlanChangesRecordTheOperatorWhoMadeThem()
    {
        var created = await factory.Services.GetRequiredService<ITenantProvisioner>()
            .CreateAsync(OperatorSession.Principal(), Unique("who"), "Who", CancellationToken.None);
        var tenantId = created.Value!.Id;
        var keys = factory.Services.GetRequiredService<IApiKeyAdmin>();
        var plans = factory.Services.GetRequiredService<IPlanAdmin>();
        var operatorUser = OperatorSession.Principal("second-operator@example.com");

        var issued = await keys.IssueAsync(operatorUser, tenantId, "prod", null, null, CancellationToken.None);
        await plans.SetAsync(operatorUser, tenantId, "standard", CancellationToken.None);
        await keys.RevokeAsync(operatorUser, tenantId, issued.Value!.ApiKey.Id, CancellationToken.None);

        foreach (var action in new[] { AuditActions.ApiKeyIssued, AuditActions.TenantPlanAssigned, AuditActions.ApiKeyRevoked })
        {
            var only = Assert.Single(await EventsAsync(factory, tenantId, action));
            Assert.Equal(AuditActors.OperatorUser, only.ActorType);
            Assert.Equal("second-operator@example.com", only.ActorId);
        }
    }

    [Fact]
    public async Task TheDetailPageShowsPlanKeysAndRecentEventsWithoutEverShowingASecret()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("detail-a");
        using var client = factory.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}");

        Assert.Contains(tenant.Identifier, html, StringComparison.Ordinal);
        Assert.Contains("<h1", html, StringComparison.Ordinal);
        Assert.Contains("standard", html, StringComparison.Ordinal); // 方案選單與目前的方案
        Assert.Contains(key.ApiKey.Prefix, html, StringComparison.Ordinal);
        Assert.DoesNotContain(key.Key, html, StringComparison.Ordinal); // 完整的 key 只在簽發當下出現
        Assert.Contains($"aria-label=\"撤銷 {key.ApiKey.Name}\"", html, StringComparison.Ordinal);
        Assert.Contains("簽發新 key", html, StringComparison.Ordinal);
        Assert.Contains("最近的稽核事件", html, StringComparison.Ordinal);
        Assert.Contains("建立租戶", html, StringComparison.Ordinal);
        Assert.Contains($"/ui/audit?tenant={tenant.Id}", html, StringComparison.Ordinal);
        Assert.Contains("<th scope=\"col\">", html, StringComparison.Ordinal);
        Assert.Contains("用量與計量", html, StringComparison.Ordinal);
        Assert.Contains("還沒有使用紀錄", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheDetailPageShowsTenantUsageSummaryWhenRecordsExist()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("detail-usage");
        await using (var scope = factory.Services.CreateAsyncScope())
        {
            var store = scope.ServiceProvider.GetRequiredService<Finbuckle.MultiTenant.Abstractions.IMultiTenantStore<Finbuckle.MultiTenant.Abstractions.TenantInfo>>();
            var tenantInfo = await store.GetAsync(tenant.Id);
            await using var db = Finbuckle.MultiTenant.EntityFrameworkCore.MultiTenantDbContext.Create<EnvCite.Platform.Data.PlatformDbContext, Finbuckle.MultiTenant.Abstractions.TenantInfo>(tenantInfo!, scope.ServiceProvider);
            db.UsageRecords.Add(new EnvCite.Platform.Data.UsageRecord { Operation = "ontology.search", Units = 1, OccurredAt = DateTimeOffset.UtcNow });
            db.UsageRecords.Add(new EnvCite.Platform.Data.UsageRecord { Operation = "consult.ground", Units = 5, OccurredAt = DateTimeOffset.UtcNow });
            await db.SaveChangesAsync(TestContext.Current.CancellationToken);
        }

        using var client = factory.SignedInClient();
        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}");

        Assert.Contains("用量與計量", html, StringComparison.Ordinal);
        Assert.Contains("本體條文檢索", html, StringComparison.Ordinal);
        Assert.Contains("諮詢接地證據集", html, StringComparison.Ordinal);
        Assert.Contains("ontology.search", html, StringComparison.Ordinal);
        Assert.Contains("consult.ground", html, StringComparison.Ordinal);
        Assert.Contains("法規本體", html, StringComparison.Ordinal);
        Assert.Contains("諮詢接地", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ARevokedKeyIsMarkedAndCannotBeRevokedAgain()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("detail-b");
        await factory.Services.GetRequiredService<IApiKeyAdmin>()
            .RevokeAsync(OperatorSession.Principal(), tenant.Id, key.ApiKey.Id, CancellationToken.None);
        using var client = factory.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}");

        Assert.Contains("已撤銷", html, StringComparison.Ordinal);
        Assert.DoesNotContain($"aria-label=\"撤銷 {key.ApiKey.Name}\"", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ATenantWithoutAPlanSaysSoAndOffersToAssignOne()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("detail-c");
        await using var host = NoDefaultPlan();
        using var client = host.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}");

        Assert.Contains("目前沒有方案", html, StringComparison.Ordinal);
        Assert.Contains("指派方案", html, StringComparison.Ordinal);
        Assert.DoesNotContain("移除方案", html, StringComparison.Ordinal); // 沒有可移除的
    }

    [Fact]
    public async Task AnUnknownTenantSaysSoInsteadOfFailing()
    {
        using var client = factory.SignedInClient();

        var html = await client.PageAsync("/ui/tenants/no-such-tenant");

        Assert.Contains("找不到這個租戶", html, StringComparison.Ordinal);
        Assert.Contains("/ui/tenants", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheDetailPageNeedsAnOperator()
    {
        using var anonymous = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

        using var response = await anonymous.GetAsync(new Uri("/ui/tenants/anything", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/ui/login", response.Headers.Location?.AbsolutePath);
    }

    [Fact]
    public async Task TheTenantListOffersCreatingATenantAndLinksToEachDetailPage()
    {
        // 租戶清單一頁 25 列、依識別碼排序；共用資料庫裡的租戶會越來越多，識別碼以 0 開頭才保證排在第一頁。
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("0list-link");
        using var client = factory.SignedInClient();

        var html = await client.PageAsync("/ui/tenants");

        Assert.Contains("新增租戶", html, StringComparison.Ordinal);
        Assert.Contains($"href=\"/ui/tenants/{tenant.Id}\"", html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheDetailPageListsMembersWithTheirStateAndNeverAPassword()
    {
        var (tenant, email, initial) = await PortalTestSupport.MemberAsync(factory, "ops-members");
        using var client = factory.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}");

        Assert.Contains("成員（客戶入口）", html, StringComparison.Ordinal);
        Assert.Contains("新增成員", html, StringComparison.Ordinal);
        Assert.Contains(email, html, StringComparison.Ordinal);
        Assert.Contains("擁有者", html, StringComparison.Ordinal); // 角色顯示中文名稱，不是 TenantOwner
        Assert.Contains("待改密碼", html, StringComparison.Ordinal); // 初始密碼還沒被成員改掉
        Assert.Contains($"/portal/{tenant.Identifier}/login", html, StringComparison.Ordinal); // 給客戶的登入網址
        Assert.Contains($"aria-label=\"重設 {email} 的密碼\"", html, StringComparison.Ordinal);
        Assert.DoesNotContain(initial, html, StringComparison.Ordinal); // 初始密碼只在建立當下顯示一次
    }

    // 詳細頁依主題分頁：一次只看一件事。五個面板都在文件裡（沒選到的收起），所以切換不必再查資料；選哪一個由網址的 ?tab= 決定。
    private static readonly string[] TabKeys = ["usage", "keys", "members", "settings", "audit"];

    private static string PanelTag(string html, string key)
    {
        var match = Regex.Match(html, $"""<div class="ep-tab-panel"[^>]*aria-labelledby="ep-tab-{key}"[^>]*>""", RegexOptions.None, TimeSpan.FromSeconds(2));
        Assert.True(match.Success, $"panel {key} is in the document");
        return match.Value;
    }

    private static bool IsOpen(string html, string key) => !Regex.IsMatch(PanelTag(html, key), @"\shidden(\s|=|>)", RegexOptions.None, TimeSpan.FromSeconds(2));

    private static bool IsCurrentTab(string html, string key) =>
        Regex.IsMatch(html, $"""<a class="ep-tab" id="ep-tab-{key}"[^>]*aria-current="page"[^>]*>""", RegexOptions.None, TimeSpan.FromSeconds(2));

    [Fact]
    public async Task TheDetailPageOpensOnTheFirstTabAndKeepsEveryPanelInTheDocument()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("detail-tabs");
        using var client = factory.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}");

        Assert.Equal(["usage"], TabKeys.Where(k => IsOpen(html, k)));
        Assert.Equal(["usage"], TabKeys.Where(k => IsCurrentTab(html, k)));
        Assert.Contains(key.ApiKey.Prefix, html, StringComparison.Ordinal); // 收起的面板仍在文件裡
        // 連結帶頁面自己的路徑（文件有 <base href="/">，只寫 ?tab= 會跳到根目錄）。
        foreach (var tab in TabKeys)
            Assert.Contains($"href=\"/ui/tenants/{tenant.Id}?tab={tab}\"", html, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("keys")]
    [InlineData("members")]
    [InlineData("settings")]
    [InlineData("audit")]
    public async Task TheTabInTheUrlIsTheOnlyOneOpen(string tab)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync($"detail-tab-{tab}");
        using var client = factory.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}?tab={tab}");

        Assert.Equal([tab], TabKeys.Where(k => IsOpen(html, k)));
        Assert.Equal([tab], TabKeys.Where(k => IsCurrentTab(html, k)));
    }

    [Fact]
    public async Task AnUnknownTabFallsBackToTheFirstOne()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("detail-tab-unknown");
        using var client = factory.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}?tab=no-such-tab");

        Assert.Equal(["usage"], TabKeys.Where(k => IsOpen(html, k)));
    }

    [Fact]
    public async Task TheSettingsTabPutsPlanAndAiAnswerSideBySideAsEqualCards()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("detail-tab-grid");
        using var client = factory.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{tenant.Id}?tab=settings");

        var grid = html.IndexOf("<div class=\"ep-grid\">", StringComparison.Ordinal);
        Assert.True(grid > 0, "the settings tab is a grid");
        var plan = html.IndexOf("id=\"plan-heading\"", grid, StringComparison.Ordinal);
        var ai = html.IndexOf("id=\"ai-heading\"", grid, StringComparison.Ordinal);
        Assert.True(plan > grid && ai > plan, "plan then AI answer, both directly in the grid");
    }
}