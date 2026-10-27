using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Resources;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// 稽核事件在畫面上的樣子：名稱（依介面語言，見 Resources/Labels）、色彩語意、操作者怎麼稱呼。色彩只是輔助，
/// 事件名稱與圖示永遠一併顯示（不只靠顏色傳達意義）。
/// </summary>
public static class AuditLabels
{
    /// <summary>事件的名稱，依目前的介面語言。未知的事件直接顯示它的鍵，不隱藏。</summary>
    public static string Action(string action) => Text.Find($"Audit.Action.{action}") ?? action;

    /// <summary>
    /// 徽章語氣（<c>StatusBadge</c> 的 tone）：建立與簽發是成功的新增、撤銷要留意、被拒絕是異常；登入登出是中性資訊。
    /// 色彩只是輔助，事件名稱永遠一併顯示。
    /// </summary>
    public static string ActionTone(string action) => action switch
    {
        AuditActions.TenantCreated or AuditActions.ApiKeyIssued or AuditActions.TenantPlanAssigned or AuditActions.MemberCreated or AuditActions.OperatorCreated => "success",
        AuditActions.ApiKeyRevoked or AuditActions.TenantPlanRemoved or AuditActions.MemberPasswordReset
            or AuditActions.PortalRecoveryCodeUsed or AuditActions.MemberMfaReset or AuditActions.PortalMfaDisabled or AuditActions.MemberDisabled or AuditActions.MemberRoleChanged or AuditActions.TenantAiAnswerEnabled
            or AuditActions.OperatorPasswordReset or AuditActions.OperatorMfaReset or AuditActions.OperatorDisabled or AuditActions.OperatorRecoveryCodeUsed => "warning",
        AuditActions.OperatorSignInDenied or AuditActions.PortalSignInFailed => "error",
        _ => "neutral",
    };

    public static string Actor(string type, string? id) => type switch
    {
        AuditActors.OperatorKey => Name("operator-key"),
        AuditActors.OperatorUser => id ?? Name("operator-user"),
        AuditActors.TenantKey => id is null ? Name("tenant-key") : Text.Format("Audit.Actor.tenant-key.id", Shorten(id)),
        AuditActors.TenantUser => id is null ? Name("tenant-user") : Text.Format("Audit.Actor.tenant-user.id", Shorten(id)),
        AuditActors.System => Name("system"),
        _ => Name("anonymous"),
    };

    private static string Name(string actor) => Text.Find($"Audit.Actor.{actor}") ?? actor;

    /// <summary>
    /// 長識別碼在表格裡只顯示結尾，完整值在展開的細節裡。要看結尾不是開頭：這些 Guid 是 UUIDv7，
    /// 前面是時間戳，同一段時間建立的都長得一樣，後面的隨機部分才分得出來。
    /// </summary>
    public static string Shorten(string id) => id.Length <= 8 ? id : "…" + id[^8..];
}