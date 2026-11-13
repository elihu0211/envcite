using System.Security.Claims;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>
/// 營運者管理租戶的 API key。營運者的請求沒有 ambient tenant，所以每個操作經 <see cref="TenantScopes"/> 明確把 context 繫結到目標租戶；
/// 稽核事件與 key 的變更同一個 <c>SaveChanges</c>（同交易）。
/// </summary>
internal sealed class ApiKeyAdmin(TenantScopes tenants, TimeProvider time) : IApiKeyAdmin
{
    public Task<AdminOutcome<IReadOnlyList<ApiKeyInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken) =>
        tenants.InAsync<IReadOnlyList<ApiKeyInfo>>(tenantId, async scope =>
        {
            await using var db = scope.CreateDb();
            var keys = await db.ApiKeys.AsNoTracking().OrderBy(k => k.CreatedAt).ToListAsync(cancellationToken);
            return AdminOutcome.Ok<IReadOnlyList<ApiKeyInfo>>(keys.Select(Info).ToList());
        });

    public Task<AdminOutcome<IssuedApiKey>> IssueAsync(
        ClaimsPrincipal actor, string tenantId, string name, string[]? scopesRequested, DateTimeOffset? expiresAt,
        CancellationToken cancellationToken) =>
        tenants.InAsync<IssuedApiKey>(tenantId, async scope =>
        {
            var now = time.GetUtcNow();
            if (string.IsNullOrWhiteSpace(name) || name.Length > 100 || expiresAt <= now)
                return AdminOutcome.Invalid<IssuedApiKey>(OutcomeCodes.ApiKeyInvalid);

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

            await using var db = scope.CreateDb();
            db.ApiKeys.Add(apiKey);
            // Detail 只放前綴與屬性，絕不放 key 本身或雜湊。
            db.StageAudit(actor, new AuditEntry(
                AuditActions.ApiKeyIssued, scope.Tenant.Id, "api_key", apiKey.Id.ToString(),
                new Dictionary<string, object?>
                {
                    ["name"] = apiKey.Name,
                    ["prefix"] = apiKey.Prefix,
                    ["scopes"] = apiKey.Scopes,
                    ["expiresAt"] = apiKey.ExpiresAt,
                }), time);
            await db.SaveChangesAsync(cancellationToken);

            return AdminOutcome.Ok(new IssuedApiKey(fullKey, Info(apiKey)));
        });

    public Task<AdminOutcome<ApiKeyInfo>> RevokeAsync(
        ClaimsPrincipal actor, string tenantId, Guid keyId, CancellationToken cancellationToken) =>
        tenants.InAsync<ApiKeyInfo>(tenantId, async scope =>
        {
            // 租戶的查詢濾鏡讓別的租戶的 key 在這裡找不到：不能用 A 租戶的 id 撤銷 B 租戶的 key。
            await using var db = scope.CreateDb();
            var key = await db.ApiKeys.SingleOrDefaultAsync(k => k.Id == keyId, cancellationToken);
            if (key is null)
                return AdminOutcome.NotFound<ApiKeyInfo>(OutcomeCodes.ApiKeyNotFound);

            if (key.RevokedAt is null)
            {
                key.RevokedAt = time.GetUtcNow();
                db.StageAudit(actor, new AuditEntry(
                    AuditActions.ApiKeyRevoked, scope.Tenant.Id, "api_key", key.Id.ToString(),
                    new Dictionary<string, object?> { ["name"] = key.Name, ["prefix"] = key.Prefix }), time);
                await db.SaveChangesAsync(cancellationToken);
            }

            return AdminOutcome.Ok(Info(key));
        });

    private static ApiKeyInfo Info(ApiKey k) => new(k.Id, k.Name, k.Prefix, k.Scopes, k.CreatedAt, k.ExpiresAt, k.RevokedAt);
}