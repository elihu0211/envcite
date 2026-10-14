namespace EnvCite.Platform.Data;

/// <summary>
/// 租戶目前的方案（權益）。不是 Finbuckle 的租戶實體：營運者跨租戶管理，每個請求也要依租戶識別碼查它。
/// 方案名稱對應設定裡的 <c>Platform:Plans</c>；誰在何時改了什麼在稽核裡（與這一列同一個交易）。
/// </summary>
public sealed class TenantEntitlement
{
    /// <summary><c>TenantInfo.Id</c>，穩定不變。</summary>
    public required string TenantId { get; init; }

    /// <summary>請求裡的租戶 claim 是識別碼，所以冗餘存一份，查詢不必再 join 租戶 store。</summary>
    public required string TenantIdentifier { get; init; }

    public required string Plan { get; set; }

    /// <summary>
    /// 這個租戶同意讓入口的問題與相關條文送到外部的模型服務產生回答（<c>Platform:Answer</c>）。
    /// 預設關閉（要同意才開）；開關跟著方案那一列，所以移除方案也一併清掉同意。
    /// </summary>
    public bool AiAnswerEnabled { get; set; }

    public DateTimeOffset UpdatedAt { get; set; }
}