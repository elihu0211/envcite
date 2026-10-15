namespace EnvCite.Platform.SharedKernel;

public enum IdempotencyState
{
    /// <summary>這個 key 在視窗內沒出現過：照常處理。</summary>
    Fresh,

    /// <summary>同 key、同內容：回放 <see cref="IdempotencyResult.Response"/>，不要再處理或計量。</summary>
    Replay,

    /// <summary>同 key、不同內容：拒絕（422）。</summary>
    Mismatch,
}

public readonly record struct IdempotencyResult(IdempotencyState State, string? Response = null);

/// <summary>
/// 為目前租戶記住帶 <c>Idempotency-Key</c> 的成功回應（由 Metering 模組實作）。
/// 只存成功的回應：錯誤不存，重送會重新處理。
/// </summary>
public interface IIdempotencyStore
{
    /// <summary><c>Idempotency-Key</c> 的長度上限。</summary>
    public const int KeyLength = 255;

    /// <summary>處理之前呼叫：這個 key 在視窗內有沒有出現過。</summary>
    Task<IdempotencyResult> LookupAsync(string operation, string key, byte[] fingerprint, CancellationToken cancellationToken);

    /// <summary>
    /// 處理成功之後呼叫：把回應與這次的用量在同一個交易裡存下。兩個相同 key 的請求同時成功時只有一個存得進去，
    /// 另一個的用量一併回滾，改回傳先到者的結果（<see cref="IdempotencyState.Replay"/>）。
    /// </summary>
    Task<IdempotencyResult> CompleteAsync(
        string operation, string key, byte[] fingerprint, string response, string usageOperation, int units,
        CancellationToken cancellationToken);
}