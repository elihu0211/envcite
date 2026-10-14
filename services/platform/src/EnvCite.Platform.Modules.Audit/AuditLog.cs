using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Modules.Audit;

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