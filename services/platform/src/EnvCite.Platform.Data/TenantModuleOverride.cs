namespace EnvCite.Platform.Data;

/// <summary>
/// 營運者針對單一租戶對特定功能模組的動態覆寫（開啟或關閉）。
/// 以 <c>TenantId</c> 外鍵依附於 <see cref="TenantEntitlement"/>，移除方案時一併級聯刪除。
/// </summary>
public sealed class TenantModuleOverride
{
    public required string TenantId { get; init; }

    public required string ModuleKey { get; init; }

    public bool Enabled { get; set; }

    public DateTimeOffset UpdatedAt { get; set; }
}