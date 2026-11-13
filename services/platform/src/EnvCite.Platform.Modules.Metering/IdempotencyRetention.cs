using Finbuckle.MultiTenant.Abstractions;

using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Modules.Metering;

/// <summary>
/// 每小時刪除已過視窗的冪等紀錄（跨租戶，所以明確略過租戶篩選：只做刪除）。
/// 失敗只記警告，下一輪再試；日誌只有刪了幾筆。
/// </summary>
public sealed partial class IdempotencyRetention(
    IServiceScopeFactory scopes,
    TimeProvider time,
    ILogger<IdempotencyRetention> logger) : BackgroundService
{
    private static readonly TimeSpan Interval = TimeSpan.FromHours(1);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(Interval, time);
        do
        {
            try
            {
                await PurgeAsync(stoppingToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                RetentionFailed(logger, ex);
            }
        }
        while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    /// <summary>刪掉過期的，回傳刪了幾筆。</summary>
    public async Task<int> PurgeAsync(CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();
        await using var scope = scopes.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        var deleted = await db.IdempotencyRecords
            .IgnoreQueryFilters([Constants.TenantToken])
            .Where(r => r.ExpiresAt <= now)
            .ExecuteDeleteAsync(cancellationToken);
        if (deleted > 0)
            Deleted(logger, deleted);

        return deleted;
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Idempotency retention failed; will retry next interval")]
    private static partial void RetentionFailed(ILogger logger, Exception exception);

    [LoggerMessage(Level = LogLevel.Information, Message = "Deleted {Count} expired idempotency records")]
    private static partial void Deleted(ILogger logger, int count);
}