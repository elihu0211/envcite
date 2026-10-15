namespace EnvCite.Platform.Data;

/// <summary>
/// 一次帶 <c>Idempotency-Key</c> 的成功呼叫的回應，供視窗內的重送原樣回放。<c>TenantId</c> 是 Finbuckle 的
/// shadow property；唯一性是（租戶、操作、key）。請求內容只存雜湊，不存原文（問題文字是客戶案件的內容）。
/// </summary>
public sealed class IdempotencyRecord
{
    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string Operation { get; init; }

    public required string Key { get; init; }

    /// <summary>請求內容的 SHA-256：同一個 key 帶不同內容時用來拒絕。</summary>
    public required byte[] Fingerprint { get; init; }

    /// <summary>第一次回應的本文（JSON）。</summary>
    public required string Response { get; init; }

    public DateTimeOffset CreatedAt { get; init; }

    public DateTimeOffset ExpiresAt { get; init; }
}