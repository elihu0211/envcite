using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>建立租戶的結果；<see cref="Id"/> 是租戶的穩定識別（稽核與其他表都用它）。</summary>
public sealed record CreatedTenant(string Id, string Identifier, string? Name);

/// <summary>
/// 營運者建立租戶。營運後台用這個實作。租戶 store 是另一個 DbContext，
/// 無法與稽核同交易：建立成功後立刻記錄，記錄失敗就讓呼叫失敗（不靜默吞掉）。
/// </summary>
public interface ITenantProvisioner
{
    Task<Outcome<CreatedTenant>> CreateAsync(
        ClaimsPrincipal actor, string identifier, string name, CancellationToken cancellationToken);
}

public sealed record ApiKeyInfo(
    Guid Id, string Name, string Prefix, string[] Scopes,
    DateTimeOffset CreatedAt, DateTimeOffset? ExpiresAt, DateTimeOffset? RevokedAt);

/// <summary>完整的 key 只在簽發時回傳一次；之後再也無法取回。</summary>
public sealed record IssuedApiKey(string Key, ApiKeyInfo ApiKey);

public sealed record PlanInfo(string Name, int PermitLimit, TimeSpan Window);

/// <param name="Previous">變更前的方案名稱；原本沒有則 null。</param>
/// <param name="Current">變更後的方案名稱；移除則 null。</param>
public sealed record PlanChange(string TenantId, string? Previous, string? Current);

/// <summary>
/// 營運者管理租戶的 API key。營運後台用這個實作；每個動作都留稽核，
/// 稽核與變更同一個交易。<c>actor</c> 是做這件事的人（由已驗證的身分決定「誰」，不接受自報）。
/// </summary>
public interface IApiKeyAdmin
{
    /// <returns>租戶不存在時 <see cref="OutcomeStatus.NotFound"/>。依建立時間排序。</returns>
    Task<Outcome<IReadOnlyList<ApiKeyInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken);

    Task<Outcome<IssuedApiKey>> IssueAsync(
        ClaimsPrincipal actor, string tenantId, string name, string[]? scopes, DateTimeOffset? expiresAt,
        CancellationToken cancellationToken);

    /// <summary>重複撤銷不改變原撤銷時間，也不重複記錄。</summary>
    Task<Outcome<ApiKeyInfo>> RevokeAsync(
        ClaimsPrincipal actor, string tenantId, Guid keyId, CancellationToken cancellationToken);
}

/// <summary>
/// 租戶的方案指派。方案的定義（額度）在設定裡（產品決定，很少變）；<b>誰用哪個方案</b>在資料庫裡
/// （每個客戶都會變），改了立即生效、不必重新部署，而且留稽核。
/// </summary>
public interface IPlanAdmin
{
    /// <summary>設定裡定義的方案。</summary>
    IReadOnlyList<PlanInfo> Plans { get; }

    /// <param name="plan">方案名稱；null 表示移除（之後該租戶沒有方案，資料端點回 402）。</param>
    Task<Outcome<PlanChange>> SetAsync(
        ClaimsPrincipal actor, string tenantId, string? plan, CancellationToken cancellationToken);

    /// <summary>
    /// 開關這個租戶的 AI 回答（問題與相關條文會送到外部模型服務，所以是要同意才開）。與稽核同一個交易；
    /// 設成已經是的值不改資料也不留紀錄。租戶沒有方案（資料庫裡沒有權益那一列）時是 <see cref="OutcomeStatus.Invalid"/>：
    /// 同意跟著方案走，先指派方案。
    /// </summary>
    Task<Outcome<bool>> SetAiAnswerAsync(
        ClaimsPrincipal actor, string tenantId, bool enabled, CancellationToken cancellationToken);
}

/// <summary>單一操作的彙總用量資訊。</summary>
public sealed record TenantUsageSummary(
    string Operation,
    int Calls,
    int Units,
    DateTimeOffset? FirstOccurredAt,
    DateTimeOffset? LastOccurredAt);

/// <summary>
/// 營運者查詢租戶用量。跨租戶查詢，依租戶識別碼向該租戶的資料表彙總。
/// </summary>
public interface IUsageAdmin
{
    Task<Outcome<IReadOnlyList<TenantUsageSummary>>> GetTenantUsageAsync(
        string tenantId,
        DateTimeOffset? since,
        CancellationToken cancellationToken);
}