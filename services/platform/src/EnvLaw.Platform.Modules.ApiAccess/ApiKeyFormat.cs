using System.Buffers.Text;
using System.Security.Cryptography;
using System.Text;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>Key 格式：<c>sk_live_{prefix:12 hex}_{secret:base64url(32 bytes)}</c>。</summary>
public static class ApiKeyFormat
{
    public const int PrefixLength = 12;
    private const string Marker = "sk_live_";

    public static (string FullKey, string Prefix, byte[] SecretHash) Generate()
    {
        var prefix = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(PrefixLength / 2));
        var secret = Base64Url.EncodeToString(RandomNumberGenerator.GetBytes(32));
        return ($"{Marker}{prefix}_{secret}", prefix, Hash(secret));
    }

    public static bool TryParse(string key, out string prefix, out string secret)
    {
        prefix = secret = string.Empty;
        var minLength = Marker.Length + PrefixLength + 2;
        if (key.Length < minLength || !key.StartsWith(Marker, StringComparison.Ordinal) || key[Marker.Length + PrefixLength] != '_')
        {
            return false;
        }

        prefix = key.Substring(Marker.Length, PrefixLength);
        secret = key[(Marker.Length + PrefixLength + 1)..];
        return true;
    }

    public static byte[] Hash(string secret) => SHA256.HashData(Encoding.UTF8.GetBytes(secret));
}