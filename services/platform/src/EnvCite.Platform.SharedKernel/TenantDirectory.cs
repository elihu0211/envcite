namespace EnvCite.Platform.SharedKernel;

/// <param name="Plan">租戶目前的方案名稱；沒有方案則 null（完全付費制下，資料端點對它回 402）。</param>
/// <param name="AiAnswer">租戶是否同意 AI 回答（問題會送到外部模型服務）；沒有方案的租戶一定是 false。</param>
public sealed record TenantSummary(string Id, string Identifier, string? Name, string? Plan, bool AiAnswer = false);

/// <summary>
/// 營運者看的租戶清單：租戶 store 與方案目錄是兩個模組，由組合根把它們接起來。
/// 每次呼叫自己開 scope（理由同 <see cref="IAuditQuery"/>）。
/// </summary>
public interface ITenantDirectory
{
    Task<IReadOnlyList<TenantSummary>> ListAsync(CancellationToken cancellationToken);

    /// <summary>以租戶 Id 取一個租戶；不存在則 null。</summary>
    Task<TenantSummary?> FindAsync(string tenantId, CancellationToken cancellationToken);
}