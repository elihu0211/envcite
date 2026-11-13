using System.Net;
using System.Security.Claims;

using EnvCite.Platform.Modules.Ontology;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group4)]
public sealed class TenantModulesTests(PlatformFactory factory)
{
    private static readonly ClaimsPrincipal Operator = new(new ClaimsIdentity(
        [new Claim(ClaimTypes.NameIdentifier, "op-modules-test"), new Claim(ClaimTypes.Email, "op@envcite.test")],
        "Test"));

    [Fact]
    public async Task OperatorCanToggleModulesAndAuditIsRecorded()
    {
        var ct = TestContext.Current.CancellationToken;
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("modules-toggle");

        using var scope = factory.Services.CreateScope();
        var planAdmin = scope.ServiceProvider.GetRequiredService<IPlanAdmin>();
        var moduleAdmin = scope.ServiceProvider.GetRequiredService<IModuleAdmin>();
        var audit = scope.ServiceProvider.GetRequiredService<IAuditQuery>();
        var modules = scope.ServiceProvider.GetRequiredService<ITenantModules>();

        Assert.True((await planAdmin.SetAsync(Operator, tenant.Id, planAdmin.Plans[0].Name, ct)).IsOk);

        // 預設方案下：法規條文檢索開啟、AI 法源問答關閉
        Assert.True(modules.IsEnabled(tenant.Identifier, ModuleKeys.LawSearch));
        Assert.False(modules.IsEnabled(tenant.Identifier, ModuleKeys.GroundedAnswer));

        // 關閉 law-search → /v2/search 立即回 403
        var disabled = await moduleAdmin.SetAsync(Operator, tenant.Id, ModuleKeys.LawSearch, false, ct);
        Assert.True(disabled.IsOk);
        Assert.False(disabled.Value!.Enabled);
        Assert.Equal(TenantModuleSource.OperatorDisabled, disabled.Value.Source);
        Assert.False(modules.IsEnabled(tenant.Identifier, ModuleKeys.LawSearch));

        using var client = factory.CreateTenantClient(key.Key);
        using (var blocked = await client.PostAsJsonAsync("/v2/search", new SearchRequest("噪音管制標準", null, 5), ct))
        {
            Assert.Equal(HttpStatusCode.Forbidden, blocked.StatusCode);
        }

        // 重設為預設 → /v2/search 恢復 200
        var reset = await moduleAdmin.SetAsync(Operator, tenant.Id, ModuleKeys.LawSearch, null, ct);
        Assert.True(reset.IsOk);
        Assert.True(reset.Value!.Enabled);
        Assert.Equal(TenantModuleSource.PlanDefault, reset.Value.Source);

        using (var restored = await client.PostAsJsonAsync("/v2/search", new SearchRequest("噪音管制標準", null, 5), ct))
        {
            Assert.Equal(HttpStatusCode.OK, restored.StatusCode);
        }

        // 稽核紀錄完整
        var events = await audit.QueryAsync(new AuditFilter(Tenant: tenant.Id, Limit: 20), ct);
        Assert.Contains(events.Items, e => e.Action == AuditActions.TenantModuleDisabled);
        Assert.Contains(events.Items, e => e.Action == AuditActions.TenantModuleReset);
    }

    [Fact]
    public async Task GroundedAnswerRequiresEvidenceGroundingDependency()
    {
        var ct = TestContext.Current.CancellationToken;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("modules-dep");

        using var scope = factory.Services.CreateScope();
        var planAdmin = scope.ServiceProvider.GetRequiredService<IPlanAdmin>();
        var moduleAdmin = scope.ServiceProvider.GetRequiredService<IModuleAdmin>();

        Assert.True((await planAdmin.SetAsync(Operator, tenant.Id, planAdmin.Plans[0].Name, ct)).IsOk);

        var disableGrounding = await moduleAdmin.SetAsync(Operator, tenant.Id, ModuleKeys.EvidenceGrounding, false, ct);
        Assert.True(disableGrounding.IsOk);

        var enableAnswer = await moduleAdmin.SetAsync(Operator, tenant.Id, ModuleKeys.GroundedAnswer, true, ct);
        Assert.False(enableAnswer.IsOk);
        Assert.Equal(OutcomeCodes.ModuleDependencyMissing, enableAnswer.Code);
    }

    [Fact]
    public async Task DisablingNormativeReasoningBlocksEvaluateEndpoint()
    {
        var ct = TestContext.Current.CancellationToken;
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("modules-reasoning");

        using var scope = factory.Services.CreateScope();
        var planAdmin = scope.ServiceProvider.GetRequiredService<IPlanAdmin>();
        var moduleAdmin = scope.ServiceProvider.GetRequiredService<IModuleAdmin>();

        Assert.True((await planAdmin.SetAsync(Operator, tenant.Id, planAdmin.Plans[0].Name, ct)).IsOk);

        var disabled = await moduleAdmin.SetAsync(Operator, tenant.Id, ModuleKeys.NormativeReasoning, false, ct);
        Assert.True(disabled.IsOk);

        using var client = factory.CreateTenantClient(key.Key);
        using var response = await client.PostAsJsonAsync("/v2/reasoning/evaluate", new { }, ct);
        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Fact]
    public async Task PortalOntologyServicesEnforceTenantModules()
    {
        var ct = TestContext.Current.CancellationToken;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("modules-portal-svc");

        using var scope = factory.Services.CreateScope();
        var planAdmin = scope.ServiceProvider.GetRequiredService<IPlanAdmin>();
        var moduleAdmin = scope.ServiceProvider.GetRequiredService<IModuleAdmin>();
        var corpus = scope.ServiceProvider.GetRequiredService<ILawCorpusService>();
        var reasoning = scope.ServiceProvider.GetRequiredService<INormativeReasoningService>();

        Assert.True((await planAdmin.SetAsync(Operator, tenant.Id, planAdmin.Plans[0].Name, ct)).IsOk);

        var portalUser = new ClaimsPrincipal(new ClaimsIdentity(
        [
            new Claim(PlatformClaimTypes.PortalUserId, "member-1"),
            new Claim(PlatformClaimTypes.TenantIdentifier, tenant.Identifier),
        ], "Portal"));

        Assert.True((await moduleAdmin.SetAsync(Operator, tenant.Id, ModuleKeys.LawCorpus, false, ct)).IsOk);
        var corpusBlocked = await corpus.BrowseAsync(portalUser, "TW-CENTRAL", "moj_pcode", "O0020001", null, ct);
        Assert.False(corpusBlocked.IsOk);
        Assert.Equal(OutcomeCodes.ModuleDisabled, corpusBlocked.Code);

        Assert.True((await moduleAdmin.SetAsync(Operator, tenant.Id, ModuleKeys.NormativeReasoning, false, ct)).IsOk);
        var reasoningBlocked = await reasoning.EvaluateAsync(
            portalUser,
            new PortalReasoningInput("2025-01-01", "2025-02-01", null, null, null, null, null),
            ct);
        Assert.False(reasoningBlocked.IsOk);
        Assert.Equal(OutcomeCodes.ModuleDisabled, reasoningBlocked.Code);
    }
}