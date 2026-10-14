using System.Diagnostics;
using System.Security.Claims;
using System.Text.Json;

using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Data;

/// <summary>
/// 一筆稽核事件。資料庫的觸發器禁止 UPDATE、DELETE、TRUNCATE：只能新增。
/// 它不是 Finbuckle 的租戶實體（沒有自動的租戶篩選）：營運者本來就要跨租戶查，
/// <see cref="SubjectTenantId"/> 是「事件關於哪個租戶」的一般欄位，所以讀取必須明確篩選。
/// </summary>
public sealed class AuditEvent
{
    public Guid Id { get; init; } = Guid.CreateVersion7();

    public DateTimeOffset OccurredAt { get; init; }

    public required string ActorType { get; init; }

    public string? ActorId { get; init; }

    public required string Action { get; init; }

    public string? SubjectTenantId { get; init; }

    public string? TargetType { get; init; }

    public string? TargetId { get; init; }

    /// <summary>JSON 物件（jsonb）。不含密鑰、雜湊或完整 API key。</summary>
    public required string Detail { get; init; }

    /// <summary>OpenTelemetry 的 trace id，把稽核事件接回當時的追蹤。</summary>
    public string? TraceId { get; init; }

    public static AuditEvent From(ClaimsPrincipal actor, AuditEntry entry, DateTimeOffset now)
    {
        var (type, id) = AuditActors.From(actor);
        return new AuditEvent
        {
            OccurredAt = now,
            ActorType = type,
            ActorId = id,
            Action = entry.Action,
            SubjectTenantId = entry.TenantId,
            TargetType = entry.TargetType,
            TargetId = entry.TargetId,
            Detail = JsonSerializer.Serialize(entry.Detail ?? new Dictionary<string, object?>()),
            TraceId = Activity.Current?.TraceId.ToString(),
        };
    }
}

public static class AuditExtensions
{
    /// <summary>
    /// 把稽核事件加進這個 context 的變更追蹤，<b>不</b>儲存：呼叫端的 <c>SaveChangesAsync</c> 會在同一個
    /// 交易裡一起寫，所以資料變更與它的稽核事件一起成功或一起失敗。
    /// </summary>
    public static void StageAudit(this PlatformDbContext db, ClaimsPrincipal actor, AuditEntry entry, TimeProvider time) =>
        db.Add(AuditEvent.From(actor, entry, time.GetUtcNow()));
}