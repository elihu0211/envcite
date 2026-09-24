namespace EnvLaw.Platform.SharedKernel;

/// <summary>平台驗證 handler 所發出的 claim type。</summary>
public static class PlatformClaimTypes
{
    /// <summary>由 API key 解析出的租戶識別碼（Finbuckle 的 <c>TenantInfo.Identifier</c>）。</summary>
    public const string TenantIdentifier = "tenant";

    /// <summary>驗證此請求所用之 API key 的 Id。</summary>
    public const string ApiKeyId = "api_key_id";

    /// <summary>每個授予的 scope 各一個 claim。</summary>
    public const string Scope = "scope";
}