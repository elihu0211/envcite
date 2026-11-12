namespace EnvCite.Platform.Data;

/// <summary>
/// 租戶的目錄同步設定與狀態，每個租戶一列。<c>Enabled</c> 預設關閉：部署者開了功能面，租戶自己也要開才生效。
/// 計數與最後請求時間是狀態（給介面顯示連線健康），不是稽核；稽核在 <see cref="AuditEvent"/>。
/// <c>TenantId</c> 是 Finbuckle 的 shadow property。
/// </summary>
public sealed class DirectoryConnection
{
    public Guid Id { get; init; } = Guid.CreateVersion7();

    public bool Enabled { get; set; }

    /// <summary>推送沒指定角色時的角色；只能是目錄同步可授予的角色。</summary>
    public string DefaultRole { get; set; } = "Viewer";

    public bool AdoptExistingMembers { get; set; }

    public DateTimeOffset? LastRequestAt { get; set; }

    /// <summary>最近一次被拒絕的結果代碼；之後有請求成功就清掉。</summary>
    public string? LastFailureCode { get; set; }

    public long CreatedCount { get; set; }

    public long UpdatedCount { get; set; }

    public long DeactivatedCount { get; set; }

    public long RejectedCount { get; set; }

    public DateTimeOffset UpdatedAt { get; set; }
}