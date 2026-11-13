namespace EnvCite.Platform.Modules.Metering;

/// <summary>
/// 營運者查詢特定租戶的用量。營運者請求沒有 ambient tenant，故經 <see cref="TenantScopes"/> 明確繫結至目標租戶的 context。
/// </summary>
internal sealed class UsageAdmin(TenantScopes tenants) : IUsageAdmin
{
    public Task<AdminOutcome<IReadOnlyList<TenantUsageSummary>>> GetTenantUsageAsync(
        string tenantId, DateTimeOffset? since, CancellationToken cancellationToken) =>
        tenants.InAsync<IReadOnlyList<TenantUsageSummary>>(tenantId, async scope =>
        {
            await using var db = scope.CreateDb();
            var query = db.UsageRecords.AsNoTracking();
            if (since is { } from)
                query = query.Where(r => r.OccurredAt >= from);

            var rows = await query
                .GroupBy(r => r.Operation)
                .Select(g => new
                {
                    Operation = g.Key,
                    Calls = g.Count(),
                    Units = g.Sum(r => r.Units),
                    First = g.Min(r => r.OccurredAt),
                    Last = g.Max(r => r.OccurredAt),
                })
                .OrderBy(u => u.Operation)
                .ToListAsync(cancellationToken);

            var list = rows
                .Select(r => new TenantUsageSummary(r.Operation, r.Calls, r.Units, r.First, r.Last))
                .ToList();

            return AdminOutcome.Ok<IReadOnlyList<TenantUsageSummary>>(list);
        });
}