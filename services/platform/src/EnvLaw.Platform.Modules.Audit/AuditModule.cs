using System.Security.Claims;
using System.Text.Json;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvLaw.Platform.Modules.Audit;

public sealed record AuditEventResponse(
    Guid Id, DateTimeOffset OccurredAt, string ActorType, string? ActorId, string Action,
    string? TenantId, string? TargetType, string? TargetId, JsonElement Detail, string? TraceId)
{
    internal static AuditEventResponse From(AuditEvent e) => new(
        e.Id, e.OccurredAt, e.ActorType, e.ActorId, e.Action, e.SubjectTenantId, e.TargetType, e.TargetId,
        JsonDocument.Parse(e.Detail).RootElement.Clone(), e.TraceId);
}

/// <param name="NextCursor">還有更舊的事件時，帶回 <c>before</c> 取下一頁；沒有則 null。</param>
public sealed record AuditEventPage(IReadOnlyList<AuditEventResponse> Items, Guid? NextCursor);

/// <summary>
/// 獨立寫入的稽核事件（自己的 scope 與交易）。與資料變更同交易的事件不經這裡，
/// 直接 <c>PlatformDbContext.StageAudit</c>。
/// </summary>
internal sealed class AuditLog(IServiceScopeFactory scopes, TimeProvider time) : IAuditLog
{
    public async Task AppendAsync(ClaimsPrincipal actor, AuditEntry entry, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        db.StageAudit(actor, entry, time);
        await db.SaveChangesAsync(cancellationToken);
    }
}

public static class AuditModule
{
    public const int DefaultPageSize = 50;
    public const int MaxPageSize = 200;

    public static IServiceCollection AddAuditModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddScoped<IAuditLog, AuditLog>();
        return services;
    }

    /// <summary>
    /// <c>GET /admin/audit-events</c>：營運者跨租戶查稽核。依 <c>tenant</c>、<c>action</c>、<c>actor</c> 篩選，
    /// 由新到舊，以 <c>before</c>（上一頁最後一筆的 Id；UUIDv7 依時間排序）翻頁。
    /// </summary>
    public static RouteGroupBuilder MapAuditAdminEndpoints(this RouteGroupBuilder admin)
    {
        admin.MapGet("/audit-events", async (
            string? tenant, string? action, string? actor, Guid? before, int? limit,
            PlatformDbContext db, CancellationToken ct) =>
        {
            var size = Math.Clamp(limit ?? DefaultPageSize, 1, MaxPageSize);

            var query = db.Set<AuditEvent>().AsNoTracking();
            if (!string.IsNullOrEmpty(tenant))
            {
                query = query.Where(e => e.SubjectTenantId == tenant);
            }

            if (!string.IsNullOrEmpty(action))
            {
                query = query.Where(e => e.Action == action);
            }

            if (!string.IsNullOrEmpty(actor))
            {
                query = query.Where(e => e.ActorId == actor);
            }

            if (before is { } cursor)
            {
                query = query.Where(e => e.Id.CompareTo(cursor) < 0);
            }

            // 多取一筆，才知道有沒有下一頁。
            var rows = await query.OrderByDescending(e => e.Id).Take(size + 1).ToListAsync(ct);
            var page = rows.Take(size).Select(AuditEventResponse.From).ToList();
            return Results.Ok(new AuditEventPage(page, rows.Count > size ? page[^1].Id : null));
        });

        return admin;
    }
}