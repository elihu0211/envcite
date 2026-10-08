using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvLaw.Platform.Modules.Audit;

public static class AuditModule
{
    public static IServiceCollection AddAuditModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddScoped<IAuditLog, AuditLog>();
        services.AddSingleton<IAuditQuery, AuditQuery>();
        return services;
    }

    /// <summary>
    /// <c>GET /admin/audit-events</c>：營運者跨租戶查稽核。依 <c>tenant</c>、<c>action</c>、<c>actor</c> 篩選，
    /// 由新到舊，以 <c>before</c>（上一頁最後一筆的 Id）翻頁。營運後台的稽核頁面用的是同一個 <see cref="IAuditQuery"/>。
    /// </summary>
    public static RouteGroupBuilder MapAuditAdminEndpoints(this RouteGroupBuilder admin)
    {
        admin.MapGet("/audit-events", async (
            string? tenant, string? action, string? actor, Guid? before, int? limit,
            IAuditQuery query, CancellationToken ct) =>
            Results.Ok(await query.QueryAsync(new AuditFilter(tenant, action, actor, before, limit), ct)));

        return admin;
    }
}