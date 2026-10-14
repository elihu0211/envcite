namespace EnvCite.Platform.SharedKernel;

/// <summary>平台驗證 handler 所發出的 claim type。</summary>
public static class PlatformClaimTypes
{
    /// <summary>由 API key 解析出的租戶識別碼（Finbuckle 的 <c>TenantInfo.Identifier</c>）。</summary>
    public const string TenantIdentifier = "tenant";

    /// <summary>驗證此請求所用之 API key 的 Id。</summary>
    public const string ApiKeyId = "api_key_id";

    /// <summary>客戶入口使用者的 Id（<c>ApplicationUser.Id</c>）。有這個 claim 的是入口的人，不是機器。</summary>
    public const string PortalUserId = "portal_user_id";

    /// <summary>營運者帳號的 Id（<c>OperatorUser.Id</c>）。有這個 claim 的是後台的人。</summary>
    public const string OperatorId = "operator_id";

    /// <summary>存在（值為 true）表示這個 session 還必須先改密碼。</summary>
    public const string PasswordChangeRequired = "pwd_change_required";

    /// <summary>存在（值為 true）表示這個 session 還必須先註冊兩步驟驗證（角色要求，但還沒啟用）。</summary>
    public const string MfaEnrollmentRequired = "mfa_enrollment_required";

    /// <summary>每個授予的 scope 各一個 claim。</summary>
    public const string Scope = "scope";
}