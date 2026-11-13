using Finbuckle.MultiTenant.Abstractions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 在指定租戶裡執行一個操作：營運者與互動式 circuit 都沒有 ambient tenant，每個操作自己開一個綁好租戶的 scope。
/// 要守住的是隔離：scope 裡只看得到目標租戶的資料、同時進行的兩個租戶不互相滲透、scope 用完即處置。
/// </summary>
[Collection(PlatformTestGroup.Group3)]
public sealed class TenantScopesTests(PlatformFactory factory)
{
    private TenantScopes Scopes => factory.Services.GetRequiredService<TenantScopes>();

    private static AdminOutcome<T> Ok<T>(T value) => AdminOutcome.Ok(value);

    [Fact]
    public async Task AnUnknownTenantIsNotFoundAndTheOperationNeverRuns()
    {
        var ran = false;

        var outcome = await Scopes.InAsync<int>("no-such-tenant", _ =>
        {
            ran = true;
            return Task.FromResult(Ok(1));
        });

        Assert.Equal(AdminStatus.NotFound, outcome.Status);
        Assert.Equal(OutcomeCodes.TenantNotFound, outcome.Code);
        Assert.False(ran);
    }

    // circuit 的身分裡只有租戶識別碼（網址與 cookie 裡的那個），不是內部 Id。
    [Fact]
    public async Task ATenantCanBeAddressedByItsIdentifierAndIsBoundJustTheSame()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("ts-f");

        var seen = await Scopes.InByIdentifierAsync<string?>(a.Identifier, scope =>
            Task.FromResult(Ok(scope.Get<IMultiTenantContextAccessor<TenantInfo>>().MultiTenantContext?.TenantInfo?.Id)));
        var unknown = await Scopes.InByIdentifierAsync<int>("no-such-tenant", _ => Task.FromResult(Ok(1)));

        Assert.Equal(a.Id, seen.Value);
        Assert.Equal(OutcomeCodes.TenantNotFound, unknown.Code);
    }

    [Fact]
    public async Task InsideTheScopeOnlyTheTargetTenantsDataIsVisible()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("ts-a");
        var (b, _) = await factory.CreateTenantWithKeyAsync("ts-b");
        var keys = factory.Services.GetRequiredService<IApiKeyAdmin>();
        var prefixesOfA = (await keys.ListAsync(a.Id, CancellationToken.None)).Value!.Select(k => k.Prefix).Order().ToList();
        var prefixesOfB = (await keys.ListAsync(b.Id, CancellationToken.None)).Value!.Select(k => k.Prefix).Order().ToList();
        Assert.NotEmpty(prefixesOfA);
        Assert.Empty(prefixesOfA.Intersect(prefixesOfB));

        // 兩條路：由 DI 解析的 context（靠剛綁定的租戶），與明確繫結的 context。
        var seen = await Scopes.InAsync<(List<string> ResolvedFromDi, List<string> Explicit)>(a.Id, async scope =>
        {
            var resolved = await scope.Get<PlatformDbContext>().ApiKeys.AsNoTracking().Select(k => k.Prefix).OrderBy(p => p).ToListAsync(TestContext.Current.CancellationToken);
            await using var explicitDb = scope.CreateDb();
            var explicitKeys = await explicitDb.ApiKeys.AsNoTracking().Select(k => k.Prefix).OrderBy(p => p).ToListAsync(TestContext.Current.CancellationToken);
            return Ok((resolved, explicitKeys));
        });

        Assert.Equal(prefixesOfA, seen.Value.ResolvedFromDi);
        Assert.Equal(prefixesOfA, seen.Value.Explicit);
    }

    // 租戶 context 是 AsyncLocal：同時進行、中途讓出執行緒的兩個租戶，各自看到的永遠是自己的租戶。
    [Fact]
    public async Task OperationsRunningAtTheSameTimeNeverSeeEachOthersTenant()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("ts-c");
        var (b, _) = await factory.CreateTenantWithKeyAsync("ts-d");

        async Task<List<string?>> Observe(string tenantId)
        {
            var observed = new List<string?>();
            var outcome = await Scopes.InAsync<int>(tenantId, async scope =>
            {
                var accessor = scope.Get<IMultiTenantContextAccessor<TenantInfo>>();
                for (var i = 0; i < 25; i++)
                {
                    await Task.Yield();
                    observed.Add(accessor.MultiTenantContext?.TenantInfo?.Id);
                }

                return Ok(0);
            });
            Assert.True(outcome.IsOk);
            return observed;
        }

        var results = await Task.WhenAll(Enumerable.Range(0, 8).Select(i => Observe(i % 2 == 0 ? a.Id : b.Id)));

        for (var i = 0; i < results.Length; i++)
            Assert.All(results[i], id => Assert.Equal(i % 2 == 0 ? a.Id : b.Id, id));
    }

    [Fact]
    public async Task TheScopeIsDisposedWhenTheOperationEnds()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("ts-e");

        var outcome = await Scopes.InAsync<PlatformDbContext>(a.Id, scope => Task.FromResult(Ok(scope.Get<PlatformDbContext>())));

        await Assert.ThrowsAsync<ObjectDisposedException>(async () =>
            await outcome.Value!.ApiKeys.CountAsync(TestContext.Current.CancellationToken));
    }
}