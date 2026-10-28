using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Metering;

public sealed record UsageSummary(string Operation, int Calls, int Units);

internal sealed class UsageRecorder(PlatformDbContext db, TimeProvider time) : IUsageRecorder
{
    public async Task RecordAsync(string operation, int units, CancellationToken cancellationToken)
    {
        Stage(operation, units);
        await db.SaveChangesAsync(cancellationToken);
    }

    public void Stage(string operation, int units) =>
        db.UsageRecords.Add(new UsageRecord { Operation = operation, Units = units, OccurredAt = time.GetUtcNow() });
}

public static class MeteringModule
{
    /// <summary>逐租戶的用量計量（每次計費呼叫一列；之後再彙總供計費）。</summary>
    public static IServiceCollection AddMeteringModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddScoped<IUsageRecorder, UsageRecorder>();
        services.AddScoped<IUsageAdmin, UsageAdmin>();
        services.AddPlatformOptions<IdempotencyOptions, IdempotencyOptionsValidator>();
        services.AddScoped<IIdempotencyStore, IdempotencyStore>();
        return services;
    }

    /// <summary>
    /// 定期刪除過期的冪等紀錄。需要資料庫，所以和對話保存期限清理一樣不在產生 OpenAPI 的模擬 host 註冊。
    /// </summary>
    public static IServiceCollection AddIdempotencyRetention(this IServiceCollection services)
    {
        services.AddSingleton<IdempotencyRetention>();
        services.AddHostedService(provider => provider.GetRequiredService<IdempotencyRetention>());
        return services;
    }

    /// <summary><c>GET /v2/usage</c>：目前租戶依 operation 分列的用量。</summary>
    public static RouteGroupBuilder MapMeteringEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/usage", async (PlatformDbContext db, CancellationToken ct) =>
            await db.UsageRecords
                .GroupBy(r => r.Operation)
                .OrderBy(g => g.Key)
                .Select(g => new UsageSummary(g.Key, g.Count(), g.Sum(r => r.Units)))
                .ToListAsync(ct))
            .WithName("GetUsage")
            .WithTags("租戶")
            .WithSummary("本租戶的用量")
            .WithDescription("依操作分列的呼叫次數與計量單位。成功的呼叫才計量；拒答是成功的呼叫。");
        return v1;
    }
}