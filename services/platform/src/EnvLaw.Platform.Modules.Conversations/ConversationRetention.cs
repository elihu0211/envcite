using EnvLaw.Platform.Data;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.Conversations;

/// <summary>
/// 每小時刪除超過保留天數的對話（跨租戶，所以明確略過租戶篩選：只有這一處，只做刪除）。
/// 失敗只記警告，下一輪再試；日誌裡沒有對話內容，只有刪了幾串。
/// </summary>
public sealed partial class ConversationRetention(
    IServiceScopeFactory scopes,
    IOptions<ConversationHistoryOptions> options,
    TimeProvider time,
    ILogger<ConversationRetention> logger) : BackgroundService
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

    /// <summary>刪掉過期的，回傳刪了幾串。沒開啟問答歷史時不動資料。</summary>
    public async Task<int> PurgeAsync(CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled)
        {
            return 0;
        }

        var cutoff = time.GetUtcNow().AddDays(-options.Value.RetentionDays);
        await using var scope = scopes.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        var deleted = await db.Conversations
            .IgnoreQueryFilters([Constants.TenantToken])
            .Where(c => c.UpdatedAt < cutoff)
            .ExecuteDeleteAsync(cancellationToken);
        if (deleted > 0)
        {
            Deleted(logger, deleted, options.Value.RetentionDays);
        }

        return deleted;
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Conversation retention failed; will retry next interval")]
    private static partial void RetentionFailed(ILogger logger, Exception exception);

    [LoggerMessage(Level = LogLevel.Information, Message = "Deleted {Count} conversations older than {Days} days")]
    private static partial void Deleted(ILogger logger, int count, int days);
}