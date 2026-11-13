namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 目錄同步 token 的格式：<c>scim_{prefix:12 hex}_{secret:base64url(32 bytes)}</c>。標記與 API key（<c>sk_live_</c>）不同，
/// 所以其中一種憑證拿去另一種的端點，連格式檢查都過不了。
/// </summary>
public static class DirectoryTokenFormat
{
    public const int PrefixLength = DirectoryToken.PrefixLength;
    private const string Marker = "scim_";

    public static (string FullKey, string Prefix, byte[] SecretHash) Generate() => BearerSecret.Generate(Marker, PrefixLength);

    public static bool TryParse(string key, out string prefix, out string secret) =>
        BearerSecret.TryParse(Marker, PrefixLength, key, out prefix, out secret);

    public static byte[] Hash(string secret) => BearerSecret.Hash(secret);
}