namespace EnvCite.Platform.Tests;

/// <summary>
/// 回答服務的設定與租戶的同意：部署者的設定在啟動就驗證；租戶的同意是營運者的動作，跟著方案走、留稽核。
/// 回答怎麼產生、怎麼核驗引用在 <see cref="AskServiceTests"/>（直接在服務層）。
/// </summary>
[Collection(PlatformTestGroup.Group1)]
public sealed class AnswerSettingsTests(PlatformFactory factory)
{
    private async Task<List<AuditRecord>> EventsAsync(string tenantId, string action) =>
        [.. (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenantId, Action: action), CancellationToken.None)).Items];

    [Fact]
    public void EnablingTheAnswerServiceWithoutItsEndpointAndKeyStopsTheHostAtStartup()
    {
        var ex = Assert.ThrowsAny<Exception>(() =>
        {
            using var bad = factory.WithWebHostBuilder(b => b.UseSetting("Platform:Answer:Enabled", "true"));
            _ = bad.Services;
        });

        Assert.Contains("Platform:Answer:Endpoint", ex.ToString(), StringComparison.Ordinal);
        Assert.Contains("Platform:Answer:ApiKey", ex.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ConsentIsAnOperatorActionThatIsAuditedIdempotentAndNeedsAPlan()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ans-c");
        var plans = factory.Services.GetRequiredService<IPlanAdmin>();
        var actor = OperatorSession.Principal();

        Assert.Equal(AdminStatus.Invalid, (await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None)).Status); // 同意跟著方案走
        Assert.Equal(AdminStatus.NotFound, (await plans.SetAiAnswerAsync(actor, "no-such", true, CancellationToken.None)).Status);

        Assert.True((await plans.SetAsync(actor, tenant.Id, "standard", CancellationToken.None)).IsOk);
        Assert.True((await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None)).IsOk);
        Assert.True((await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None)).IsOk);

        Assert.Single(await EventsAsync(tenant.Id, AuditActions.TenantAiAnswerEnabled)); // 重複開不重複記
        Assert.True((await factory.Services.GetRequiredService<ITenantDirectory>().FindAsync(tenant.Id, CancellationToken.None))!.AiAnswer);

        Assert.True((await plans.SetAiAnswerAsync(actor, tenant.Id, false, CancellationToken.None)).IsOk);
        Assert.Single(await EventsAsync(tenant.Id, AuditActions.TenantAiAnswerDisabled));

        // 再開一次，然後移除方案：同意跟著那一列一起消失，不會殘留到下一個方案。
        await plans.SetAiAnswerAsync(actor, tenant.Id, true, CancellationToken.None);
        Assert.True((await plans.SetAsync(actor, tenant.Id, null, CancellationToken.None)).IsOk);
        Assert.True((await plans.SetAsync(actor, tenant.Id, "standard", CancellationToken.None)).IsOk);
        Assert.False((await factory.Services.GetRequiredService<ITenantDirectory>().FindAsync(tenant.Id, CancellationToken.None))!.AiAnswer);
    }
}