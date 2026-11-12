using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>Key 格式：<c>sk_live_{prefix:12 hex}_{secret:base64url(32 bytes)}</c>。格式本身在 <see cref="BearerSecret"/>，與目錄同步的 token 共用。</summary>
public static class ApiKeyFormat
{
    public const int PrefixLength = ApiKey.PrefixLength;
    private const string Marker = "sk_live_";

    public static (string FullKey, string Prefix, byte[] SecretHash) Generate() => BearerSecret.Generate(Marker, PrefixLength);

    public static bool TryParse(string key, out string prefix, out string secret) =>
        BearerSecret.TryParse(Marker, PrefixLength, key, out prefix, out secret);

    public static byte[] Hash(string secret) => BearerSecret.Hash(secret);
}