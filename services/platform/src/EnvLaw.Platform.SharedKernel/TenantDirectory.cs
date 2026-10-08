namespace EnvLaw.Platform.SharedKernel;

/// <param name="Plan">租戶目前的方案名稱；沒有方案則 null（完全付費制下，資料端點對它回 402）。</param>
public sealed record TenantSummary(string Id, string Identifier, string? Name, string? Plan);

/// <summary>
/// 營運者看的租戶清單：租戶 store 與方案目錄是兩個模組，由組合根把它們接起來。
/// 每次呼叫自己開 scope（理由同 <see cref="IAuditQuery"/>）。
/// </summary>
public interface ITenantDirectory
{
    Task<IReadOnlyList<TenantSummary>> ListAsync(CancellationToken cancellationToken);
}