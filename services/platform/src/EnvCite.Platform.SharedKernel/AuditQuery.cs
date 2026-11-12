using System.Text.Json;

namespace EnvCite.Platform.SharedKernel;

/// <summary>穩定的稽核事件鍵。寫入端與讀取端（篩選、顯示）都用這裡，不各寫各的字串。</summary>
public static class AuditActions
{
    public const string TenantCreated = "tenant.created";
    public const string ApiKeyIssued = "api_key.issued";
    public const string ApiKeyRevoked = "api_key.revoked";
    public const string TenantPlanAssigned = "tenant.plan_assigned";
    public const string TenantPlanRemoved = "tenant.plan_removed";
    public const string OperatorSignedIn = "operator.signed_in";
    public const string OperatorSignInDenied = "operator.sign_in_denied";
    public const string OperatorSignedOut = "operator.signed_out";
    public const string MemberCreated = "member.created";
    public const string MemberPasswordReset = "member.password_reset";
    public const string PortalSignedIn = "portal.signed_in";
    public const string PortalSignInFailed = "portal.sign_in_failed";
    public const string PortalPasswordChanged = "portal.password_changed";
    public const string PortalSignedOut = "portal.signed_out";
    public const string PortalMfaEnabled = "portal.mfa_enabled";
    public const string PortalRecoveryCodeUsed = "portal.recovery_code_used";
    public const string MemberMfaReset = "member.mfa_reset";
    public const string PortalMfaDisabled = "portal.mfa_disabled";
    public const string PortalRecoveryCodesRegenerated = "portal.recovery_codes_regenerated";
    public const string MemberDisabled = "member.disabled";
    public const string MemberEnabled = "member.enabled";
    public const string MemberRoleChanged = "member.role_changed";
    public const string ConversationDeleted = "conversation.deleted";
    public const string TenantAiAnswerEnabled = "tenant.ai_answer_enabled";
    public const string TenantAiAnswerDisabled = "tenant.ai_answer_disabled";
    public const string OperatorCreated = "operator.created";
    public const string OperatorPasswordReset = "operator.password_reset";
    public const string OperatorMfaReset = "operator.mfa_reset";
    public const string OperatorDisabled = "operator.disabled";
    public const string OperatorEnabled = "operator.enabled";
    public const string OperatorPasswordChanged = "operator.password_changed";
    public const string OperatorMfaEnabled = "operator.mfa_enabled";
    public const string OperatorRecoveryCodeUsed = "operator.recovery_code_used";
    public const string OperatorRecoveryCodesRegenerated = "operator.recovery_codes_regenerated";
    public const string MemberInvited = "member.invited";
    public const string MemberInvitationResent = "member.invitation_resent";
    public const string MemberActivated = "member.activated";
    public const string OperatorInvited = "operator.invited";
    public const string OperatorInvitationResent = "operator.invitation_resent";
    public const string OperatorActivated = "operator.activated";
    public const string DirectoryTokenIssued = "directory.token_issued";
    public const string DirectoryTokenRevoked = "directory.token_revoked";
    public const string DirectoryEnabled = "directory.enabled";
    public const string DirectoryDisabled = "directory.disabled";
    public const string DirectoryConfigured = "directory.configured";
    public const string DirectorySyncRejected = "directory.sync_rejected";
    public const string MemberProvisioned = "member.provisioned";
    public const string MemberDeprovisioned = "member.deprovisioned";
    public const string MemberReprovisioned = "member.reprovisioned";
    public const string MemberAttributeSynced = "member.attribute_synced";
    public const string MemberAdopted = "member.adopted";
    public const string MemberReleaseManaged = "member.release_managed";

    /// <summary>目前會寫入的所有事件，供篩選選單使用。</summary>
    public static IReadOnlyList<string> All { get; } =
    [
        TenantCreated, TenantPlanAssigned, TenantPlanRemoved, ApiKeyIssued, ApiKeyRevoked, OperatorSignedIn, OperatorSignInDenied, OperatorSignedOut,
        MemberCreated, MemberPasswordReset, PortalSignedIn, PortalSignInFailed, PortalPasswordChanged, PortalSignedOut,
        PortalMfaEnabled, PortalRecoveryCodeUsed, MemberMfaReset,
        PortalMfaDisabled, PortalRecoveryCodesRegenerated, MemberDisabled, MemberEnabled, MemberRoleChanged,
        ConversationDeleted, TenantAiAnswerEnabled, TenantAiAnswerDisabled,
        OperatorCreated, OperatorPasswordReset, OperatorMfaReset, OperatorDisabled, OperatorEnabled, OperatorPasswordChanged,
        OperatorMfaEnabled, OperatorRecoveryCodeUsed, OperatorRecoveryCodesRegenerated,
        MemberInvited, MemberInvitationResent, MemberActivated, OperatorInvited, OperatorInvitationResent, OperatorActivated,
        DirectoryTokenIssued, DirectoryTokenRevoked, DirectoryEnabled, DirectoryDisabled, DirectoryConfigured, DirectorySyncRejected,
        MemberProvisioned, MemberDeprovisioned, MemberReprovisioned, MemberAttributeSynced, MemberAdopted, MemberReleaseManaged,
    ];
}

/// <param name="Tenant">事件所關於的租戶 Id；空 = 不篩選。</param>
/// <param name="Before">上一頁最後一筆的 Id（UUIDv7，依時間排序）；空 = 從最新開始。</param>
/// <param name="Limit">每頁筆數；預設 50，上限 200。</param>
public sealed record AuditFilter(string? Tenant = null, string? Action = null, string? Actor = null, Guid? Before = null, int? Limit = null);

public sealed record AuditRecord(
    Guid Id, DateTimeOffset OccurredAt, string ActorType, string? ActorId, string Action,
    string? TenantId, string? TargetType, string? TargetId, JsonElement Detail, string? TraceId);

/// <param name="NextCursor">還有更舊的事件時，帶回 <c>Before</c> 取下一頁；沒有則 null。</param>
public sealed record AuditPage(IReadOnlyList<AuditRecord> Items, Guid? NextCursor);

/// <summary>
/// 讀稽核事件（新到舊、keyset 翻頁）。管理端點與營運後台共用這個實作；每次呼叫自己開 scope，
/// 所以 Blazor 的長壽命連線不會持有過期的 DbContext。
/// </summary>
public interface IAuditQuery
{
    public const int DefaultPageSize = 50;
    public const int MaxPageSize = 200;

    Task<AuditPage> QueryAsync(AuditFilter filter, CancellationToken cancellationToken);
}