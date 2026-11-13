using System.Security.Claims;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.AspNetCore.Extensions;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.ApiAccess;

public sealed record ApiKeySummary(Guid Id, string Name, string Prefix, string[] Scopes,
    DateTimeOffset CreatedAt, DateTimeOffset? ExpiresAt, DateTimeOffset? RevokedAt)
{
    internal static ApiKeySummary From(ApiKey k) => new(k.Id, k.Name, k.Prefix, k.Scopes, k.CreatedAt, k.ExpiresAt, k.RevokedAt);
}

/// <summary>僅於建立時回傳一次；之後再也無法取回 <see cref="Key"/>。</summary>
public sealed record CreatedApiKey(string Key, ApiKeySummary ApiKey);

public static class ApiAccessEndpoints
{
    /// <summary><c>/v1</c> 之下以租戶為範圍的 key 管理；查詢會自動依租戶篩選。</summary>
    public static RouteGroupBuilder MapApiAccessEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/api-keys", async (PlatformDbContext db, CancellationToken ct) =>
            (await db.ApiKeys.AsNoTracking().OrderBy(k => k.CreatedAt).ToListAsync(ct)).Select(ApiKeySummary.From))
            .WithName("ListApiKeys")
            .WithTags("租戶")
            .WithSummary("列出本租戶的 API key")
            .WithDescription("依建立時間排序；只回傳前綴，不回傳 key 本身。");

        v1.MapDelete("/api-keys/{id:guid}", async (
            Guid id, PlatformDbContext db, TimeProvider time, HttpContext context, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var key = await db.ApiKeys.SingleOrDefaultAsync(k => k.Id == id, ct);
            if (key is null)
                return Results.NotFound();

            // 重複撤銷不改變原撤銷時間，也不重複記錄。
            if (key.RevokedAt is null)
            {
                key.RevokedAt = time.GetUtcNow();
                db.StageAudit(user, new AuditEntry(
                    AuditActions.ApiKeyRevoked, context.GetMultiTenantContext<TenantInfo>().TenantInfo?.Id, "api_key", key.Id.ToString(),
                    new Dictionary<string, object?> { ["name"] = key.Name, ["prefix"] = key.Prefix }), time);
            }

            await db.SaveChangesAsync(ct);
            return Results.NoContent();
        })
            .WithName("RevokeApiKey")
            .WithTags("租戶")
            .WithSummary("撤銷 API key")
            .WithDescription("撤銷後立即失效；重複撤銷不改變原撤銷時間。")
            .Produces(StatusCodes.Status204NoContent)
            .Produces(StatusCodes.Status404NotFound);

        return v1;
    }
}