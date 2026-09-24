using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Routing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvLaw.Platform.Modules.Metering;

public sealed record UsageSummary(string Operation, int Calls, int Units);

internal sealed class UsageRecorder(MeteringDbContext db, TimeProvider time) : IUsageRecorder
{
    public async Task RecordAsync(string operation, int units, CancellationToken cancellationToken)
    {
        db.UsageRecords.Add(new UsageRecord { Operation = operation, Units = units, OccurredAt = time.GetUtcNow() });
        await db.SaveChangesAsync(cancellationToken);
    }
}

public static class MeteringModule
{
    /// <summary>逐租戶的用量計量（每次計費呼叫一列；之後再彙總供計費）。</summary>
    public static IServiceCollection AddMeteringModule(this IServiceCollection services, string connectionString)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddDbContext<MeteringDbContext>(options => options.UseNpgsql(
            connectionString,
            npgsql => npgsql.MigrationsHistoryTable("__EFMigrationsHistory", MeteringDbContext.Schema)));
        services.AddScoped<IUsageRecorder, UsageRecorder>();
        return services;
    }

    /// <summary><c>GET /v1/usage</c>：目前租戶依 operation 分列的用量。</summary>
    public static RouteGroupBuilder MapMeteringEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/usage", async (MeteringDbContext db, CancellationToken ct) =>
            await db.UsageRecords
                .GroupBy(r => r.Operation)
                .OrderBy(g => g.Key)
                .Select(g => new UsageSummary(g.Key, g.Count(), g.Sum(r => r.Units)))
                .ToListAsync(ct));
        return v1;
    }
}