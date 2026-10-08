using System.Text.Json;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Audit;

/// <summary>
/// 新到舊，以 UUIDv7 <c>Id</c> 做 keyset 翻頁（游標穩定、深頁不變慢）。AuditEvent 不是租戶實體，
/// 沒有自動的租戶篩選，所以租戶條件是這裡明確加的。
/// </summary>
internal sealed class AuditQuery(IServiceScopeFactory scopes) : IAuditQuery
{
    public async Task<AuditPage> QueryAsync(AuditFilter filter, CancellationToken cancellationToken)
    {
        var size = Math.Clamp(filter.Limit ?? IAuditQuery.DefaultPageSize, 1, IAuditQuery.MaxPageSize);

        await using var scope = scopes.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();

        var query = db.Set<AuditEvent>().AsNoTracking();
        if (!string.IsNullOrEmpty(filter.Tenant))
        {
            query = query.Where(e => e.SubjectTenantId == filter.Tenant);
        }

        if (!string.IsNullOrEmpty(filter.Action))
        {
            query = query.Where(e => e.Action == filter.Action);
        }

        if (!string.IsNullOrEmpty(filter.Actor))
        {
            query = query.Where(e => e.ActorId == filter.Actor);
        }

        if (filter.Before is { } cursor)
        {
            query = query.Where(e => e.Id.CompareTo(cursor) < 0);
        }

        // 多取一筆，才知道有沒有下一頁。
        var rows = await query.OrderByDescending(e => e.Id).Take(size + 1).ToListAsync(cancellationToken);
        var items = rows.Take(size).Select(ToRecord).ToList();
        return new AuditPage(items, rows.Count > size ? items[^1].Id : null);
    }

    private static AuditRecord ToRecord(AuditEvent e) => new(
        e.Id, e.OccurredAt, e.ActorType, e.ActorId, e.Action, e.SubjectTenantId, e.TargetType, e.TargetId,
        JsonDocument.Parse(e.Detail).RootElement.Clone(), e.TraceId);
}