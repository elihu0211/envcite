namespace EnvCite.Platform.SharedKernel;

/// <summary>對外 REST API 的路徑慣例。端點群組與營運後台顯示的 API Base URL 共用這一個值。</summary>
public static class ApiPaths
{
    public const string V2 = "/v2";
}

/// <summary>目錄同步（SCIM 2.0）的路徑。不在 <see cref="ApiPaths.V2"/> 之下：它有自己的憑證、限流，也不計量。</summary>
public static class ScimPaths
{
    public const string Prefix = "/scim/v2";
}