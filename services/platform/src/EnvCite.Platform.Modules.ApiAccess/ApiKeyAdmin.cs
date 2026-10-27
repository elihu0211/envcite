using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>
/// 營運者管理租戶的 API key。營運者的請求沒有 ambient tenant，所以每次明確把 context 繫結到目標租戶；
/// 稽核事件與 key 的變更同一個 <c>SaveChanges</c>（同交易）。
/// </summary>
internal sealed class ApiKeyAdmin(IServiceScopeFactory scopes, TimeProvider time) : IApiKeyAdmin
{
    public async Task<AdminOutcome<IReadOnlyList<ApiKeyInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await TenantAsync(scope, tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<IReadOnlyList<ApiKeyInfo>>(OutcomeCodes.TenantNotFound);
        }

        await using var db = MultiTenantDbContext.Create<PlatformDbContext, TenantInfo>(tenant, scope.ServiceProvider);
        var keys = await db.ApiKeys.AsNoTracking().OrderBy(k => k.CreatedAt).ToListAsync(cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<ApiKeyInfo>>(keys.Select(Info).ToList());
    }

    public async Task<AdminOutcome<IssuedApiKey>> IssueAsync(
        ClaimsPrincipal actor, string tenantId, string name, string[]? scopesRequested, DateTimeOffset? expiresAt,
        CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await TenantAsync(scope, tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<IssuedApiKey>(OutcomeCodes.TenantNotFound);
        }

        var now = time.GetUtcNow();
        if (string.IsNullOrWhiteSpace(name) || name.Length > 100 || expiresAt <= now)
        {
            return AdminOutcome.Invalid<IssuedApiKey>(OutcomeCodes.ApiKeyInvalid);
        }

        var (fullKey, prefix, hash) = ApiKeyFormat.Generate();
        var apiKey = new ApiKey
        {
            Name = name.Trim(),
            Prefix = prefix,
            SecretHash = hash,
            Scopes = scopesRequested ?? [],
            CreatedAt = now,
            ExpiresAt = expiresAt,
        };

        await using var db = MultiTenantDbContext.Create<PlatformDbContext, TenantInfo>(tenant, scope.ServiceProvider);
        db.ApiKeys.Add(apiKey);
        // Detail 只放前綴與屬性，絕不放 key 本身或雜湊。
        db.StageAudit(actor, new AuditEntry(
            AuditActions.ApiKeyIssued, tenant.Id, "api_key", apiKey.Id.ToString(),
            new Dictionary<string, object?>
            {
                ["name"] = apiKey.Name,
                ["prefix"] = apiKey.Prefix,
                ["scopes"] = apiKey.Scopes,
                ["expiresAt"] = apiKey.ExpiresAt,
            }), time);
        await db.SaveChangesAsync(cancellationToken);

        return AdminOutcome.Ok(new IssuedApiKey(fullKey, Info(apiKey)));
    }

    public async Task<AdminOutcome<ApiKeyInfo>> RevokeAsync(
        ClaimsPrincipal actor, string tenantId, Guid keyId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await TenantAsync(scope, tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<ApiKeyInfo>(OutcomeCodes.TenantNotFound);
        }

        // 租戶的查詢濾鏡讓別的租戶的 key 在這裡找不到：不能用 A 租戶的 id 撤銷 B 租戶的 key。
        await using var db = MultiTenantDbContext.Create<PlatformDbContext, TenantInfo>(tenant, scope.ServiceProvider);
        var key = await db.ApiKeys.SingleOrDefaultAsync(k => k.Id == keyId, cancellationToken);
        if (key is null)
        {
            return AdminOutcome.NotFound<ApiKeyInfo>(OutcomeCodes.ApiKeyNotFound);
        }

        if (key.RevokedAt is null)
        {
            key.RevokedAt = time.GetUtcNow();
            db.StageAudit(actor, new AuditEntry(
                AuditActions.ApiKeyRevoked, tenant.Id, "api_key", key.Id.ToString(),
                new Dictionary<string, object?> { ["name"] = key.Name, ["prefix"] = key.Prefix }), time);
            await db.SaveChangesAsync(cancellationToken);
        }

        return AdminOutcome.Ok(Info(key));
    }

    private static Task<TenantInfo?> TenantAsync(AsyncServiceScope scope, string tenantId) =>
        scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>().GetAsync(tenantId);

    private static ApiKeyInfo Info(ApiKey k) => new(k.Id, k.Name, k.Prefix, k.Scopes, k.CreatedAt, k.ExpiresAt, k.RevokedAt);
}