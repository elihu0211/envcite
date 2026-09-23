namespace EnvLaw.Platform.SharedKernel;

/// <summary>Claim types issued by platform authentication handlers.</summary>
public static class PlatformClaimTypes
{
    /// <summary>Tenant identifier (Finbuckle <c>TenantInfo.Identifier</c>) resolved from the API key.</summary>
    public const string TenantIdentifier = "tenant";

    /// <summary>Id of the API key that authenticated the request.</summary>
    public const string ApiKeyId = "api_key_id";

    /// <summary>One claim per granted scope.</summary>
    public const string Scope = "scope";
}