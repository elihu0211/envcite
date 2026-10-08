using System.Security.Claims;

namespace EnvLaw.Platform.SharedKernel;

public enum AdminStatus
{
    Ok,
    NotFound,

    /// <summary>輸入不合法（例如名稱空白、方案不存在）；<see cref="AdminOutcome{T}.Message"/> 說明原因。</summary>
    Invalid,
}

/// <summary>營運者操作的結果。輸入錯誤與找不到是預期的結果，不是例外。</summary>
public sealed record AdminOutcome<T>(AdminStatus Status, T? Value = default, string? Message = null)
{
    public bool IsOk => Status == AdminStatus.Ok;
}

/// <summary><see cref="AdminOutcome{T}"/> 的工廠（泛型型別上不放靜態成員）。</summary>
public static class AdminOutcome
{
    public static AdminOutcome<T> Ok<T>(T value) => new(AdminStatus.Ok, value);

    public static AdminOutcome<T> NotFound<T>(string? message = null) => new(AdminStatus.NotFound, default, message);

    public static AdminOutcome<T> Invalid<T>(string message) => new(AdminStatus.Invalid, default, message);
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
/// 營運者管理租戶的 API key。營運後台與 <c>/admin</c> 端點共用這個實作；每個動作都留稽核，
/// 稽核與變更同一個交易。<paramref name="actor"/> 是做這件事的人（由已驗證的身分決定「誰」，不接受自報）。
/// </summary>
public interface IApiKeyAdmin
{
    /// <returns>租戶不存在時 <see cref="AdminStatus.NotFound"/>。依建立時間排序。</returns>
    Task<AdminOutcome<IReadOnlyList<ApiKeyInfo>>> ListAsync(string tenantId, CancellationToken cancellationToken);

    Task<AdminOutcome<IssuedApiKey>> IssueAsync(
        ClaimsPrincipal actor, string tenantId, string name, string[]? scopes, DateTimeOffset? expiresAt,
        CancellationToken cancellationToken);

    /// <summary>重複撤銷不改變原撤銷時間，也不重複記錄。</summary>
    Task<AdminOutcome<ApiKeyInfo>> RevokeAsync(
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
    Task<AdminOutcome<PlanChange>> SetAsync(
        ClaimsPrincipal actor, string tenantId, string? plan, CancellationToken cancellationToken);
}