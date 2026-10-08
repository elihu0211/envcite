using EnvLaw.Platform.SharedKernel;

using MudBlazor;

namespace EnvLaw.Platform.Web.Presentation;

/// <summary>
/// 稽核事件在畫面上的樣子：中文名稱、色彩語意、操作者怎麼稱呼。色彩只是輔助，
/// 事件名稱與圖示永遠一併顯示（不只靠顏色傳達意義）。
/// </summary>
public static class AuditLabels
{
    public static string Action(string action) => action switch
    {
        AuditActions.TenantCreated => "建立租戶",
        AuditActions.ApiKeyIssued => "簽發 API key",
        AuditActions.ApiKeyRevoked => "撤銷 API key",
        AuditActions.OperatorSignedIn => "營運者登入",
        AuditActions.OperatorSignInDenied => "拒絕登入",
        AuditActions.OperatorSignedOut => "營運者登出",
        _ => action,
    };

    /// <summary>建立與簽發是成功的新增、撤銷要留意、被拒絕是異常；登入登出是中性資訊。</summary>
    public static Color ActionColor(string action) => action switch
    {
        AuditActions.TenantCreated or AuditActions.ApiKeyIssued => Color.Success,
        AuditActions.ApiKeyRevoked => Color.Warning,
        AuditActions.OperatorSignInDenied => Color.Error,
        _ => Color.Default,
    };

    public static string ActionIcon(string action) => action switch
    {
        AuditActions.TenantCreated => Icons.Material.Filled.AddBusiness,
        AuditActions.ApiKeyIssued => Icons.Material.Filled.VpnKey,
        AuditActions.ApiKeyRevoked => Icons.Material.Filled.KeyOff,
        AuditActions.OperatorSignInDenied => Icons.Material.Filled.GppBad,
        AuditActions.OperatorSignedIn => Icons.Material.Filled.Login,
        AuditActions.OperatorSignedOut => Icons.Material.Filled.Logout,
        _ => Icons.Material.Filled.History,
    };

    public static string Actor(string type, string? id) => type switch
    {
        AuditActors.OperatorKey => "營運者金鑰",
        AuditActors.OperatorUser => id ?? "營運者",
        AuditActors.TenantKey => id is null ? "租戶 API key" : $"租戶 API key {Shorten(id)}",
        AuditActors.System => "系統",
        _ => "匿名",
    };

    /// <summary>
    /// 長識別碼在表格裡只顯示結尾，完整值在展開的細節裡。要看結尾不是開頭：這些 Guid 是 UUIDv7，
    /// 前面是時間戳，同一段時間建立的都長得一樣，後面的隨機部分才分得出來。
    /// </summary>
    public static string Shorten(string id) => id.Length <= 8 ? id : "…" + id[^8..];
}