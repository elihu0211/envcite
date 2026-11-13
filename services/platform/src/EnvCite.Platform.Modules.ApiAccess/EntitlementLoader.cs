using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>立刻、同步地從資料庫重新載入方案指派（測試與營運操作用；平常由背景工作定期做）。</summary>
public interface IEntitlementRefresher
{
    Task RefreshAsync(CancellationToken cancellationToken);
}

/// <summary>
/// 啟動時載入一次，之後定期重新載入，讓每個副本都看到營運者在別處做的變更。重新載入失敗時保留上一份快照
/// （資料庫短暫不通不該讓所有租戶瞬間沒有方案）；啟動時載入失敗則記錄並重試，期間退回設定的方案。
/// </summary>
internal sealed partial class EntitlementLoader(
    IServiceScopeFactory scopes, PlanCatalog catalog, TenantModuleCatalog modules, IOptions<ApiAccessOptions> options, ILogger<EntitlementLoader> logger)
    : BackgroundService, IEntitlementRefresher
{
    public async Task RefreshAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        var rows = await db.Set<TenantEntitlement>().AsNoTracking().ToListAsync(cancellationToken);
        var overrides = await db.Set<TenantModuleOverride>().AsNoTracking().ToListAsync(cancellationToken);

        var unknown = rows.Where(r => !catalog.Plans.ContainsKey(r.Plan)).ToList();
        foreach (var row in unknown)
            LogUnknownPlan(logger, row.TenantIdentifier, row.Plan);

        catalog.Replace(rows.Select(r => KeyValuePair.Create(r.TenantIdentifier, r.Plan)));

        var idToIdentifier = rows.ToDictionary(r => r.TenantId, r => r.TenantIdentifier, StringComparer.Ordinal);
        modules.Replace(overrides
            .Where(o => idToIdentifier.ContainsKey(o.TenantId))
            .Select(o => (idToIdentifier[o.TenantId], o.ModuleKey, o.Enabled)));
    }

    public override async Task StartAsync(CancellationToken cancellationToken)
    {
        // 第一份快照在開始接請求之前載入；失敗不擋啟動（健康檢查本來就會擋資料庫不通的 pod）。
        try
        {
            await RefreshAsync(cancellationToken);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            LogRefreshFailed(logger, exception);
        }

        await base.StartAsync(cancellationToken);
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(options.Value.EntitlementRefresh);
        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            try
            {
                await RefreshAsync(stoppingToken);
            }
            catch (Exception exception) when (exception is not OperationCanceledException)
            {
                LogRefreshFailed(logger, exception);
            }
        }
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Could not refresh tenant plan assignments; keeping the last snapshot.")]
    private static partial void LogRefreshFailed(ILogger logger, Exception exception);

    [LoggerMessage(Level = LogLevel.Warning,
        Message = "Tenant {Tenant} is assigned plan '{Plan}', which is not defined in Platform:Plans; it is treated as having no plan.")]
    private static partial void LogUnknownPlan(ILogger logger, string tenant, string plan);
}