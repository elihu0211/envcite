using EnvLaw.Platform.Data;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.EntityFrameworkCore;

namespace EnvLaw.Platform.Modules.ApiAccess;

public sealed record CreateApiKeyRequest(string Name, string[]? Scopes, DateTimeOffset? ExpiresAt);

public sealed record ApiKeySummary(Guid Id, string Name, string Prefix, string[] Scopes,
    DateTimeOffset CreatedAt, DateTimeOffset? ExpiresAt, DateTimeOffset? RevokedAt)
{
    internal static ApiKeySummary From(ApiKey k) => new(k.Id, k.Name, k.Prefix, k.Scopes, k.CreatedAt, k.ExpiresAt, k.RevokedAt);
}

/// <summary>僅於建立時回傳一次；之後再也無法取回 <see cref="Key"/>。</summary>
public sealed record CreatedApiKey(string Key, ApiKeySummary ApiKey);

public static class ApiAccessEndpoints
{
    /// <summary><c>/admin</c> 之下的營運者端點。</summary>
    public static RouteGroupBuilder MapApiAccessAdminEndpoints(this RouteGroupBuilder admin)
    {
        admin.MapPost("/tenants/{tenantId}/api-keys", IssueKeyAsync);
        return admin;
    }

    /// <summary><c>/v1</c> 之下以租戶為範圍的 key 管理；查詢會自動依租戶篩選。</summary>
    public static RouteGroupBuilder MapApiAccessEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/api-keys", async (PlatformDbContext db, CancellationToken ct) =>
            (await db.ApiKeys.AsNoTracking().OrderBy(k => k.CreatedAt).ToListAsync(ct)).Select(ApiKeySummary.From));

        v1.MapDelete("/api-keys/{id:guid}", async (Guid id, PlatformDbContext db, TimeProvider time, CancellationToken ct) =>
        {
            var key = await db.ApiKeys.SingleOrDefaultAsync(k => k.Id == id, ct);
            if (key is null)
            {
                return Results.NotFound();
            }

            key.RevokedAt ??= time.GetUtcNow();
            await db.SaveChangesAsync(ct);
            return Results.NoContent();
        });

        return v1;
    }

    private static async Task<IResult> IssueKeyAsync(
        string tenantId, CreateApiKeyRequest request, IMultiTenantStore<TenantInfo> store,
        IServiceProvider services, TimeProvider time, CancellationToken ct)
    {
        if (await store.GetAsync(tenantId) is not { } tenant)
        {
            return Results.NotFound();
        }

        var now = time.GetUtcNow();
        if (string.IsNullOrWhiteSpace(request.Name) || request.Name.Length > 100 || request.ExpiresAt <= now)
        {
            return Results.ValidationProblem(new Dictionary<string, string[]>
            {
                ["request"] = ["Name is required (max 100 chars); ExpiresAt must be in the future."],
            });
        }

        var (fullKey, prefix, hash) = ApiKeyFormat.Generate();
        var apiKey = new ApiKey
        {
            Name = request.Name.Trim(),
            Prefix = prefix,
            SecretHash = hash,
            Scopes = request.Scopes ?? [],
            CreatedAt = now,
            ExpiresAt = request.ExpiresAt,
        };

        // Admin 請求沒有 ambient tenant，因此明確地將 context 繫結至目標租戶。
        await using var db = MultiTenantDbContext.Create<PlatformDbContext, TenantInfo>(tenant, services);
        db.ApiKeys.Add(apiKey);
        await db.SaveChangesAsync(ct);

        return Results.Created($"/v1/api-keys/{apiKey.Id}", new CreatedApiKey(fullKey, ApiKeySummary.From(apiKey)));
    }
}