using System.Buffers.Text;
using System.Security.Cryptography;
using System.Text;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 持有人憑證的格式：<c>{標記}{prefix:十六進位}_{secret:base64url(32 位元組)}</c>。前綴可公開、用來查找；
/// 只儲存 secret 的 SHA-256，完整憑證只在簽發時顯示一次。API key（<c>sk_live_</c>）與目錄同步的 token（<c>scim_</c>）
/// 共用這一份，標記不同，所以兩種憑證互不通用。
/// </summary>
public static class BearerSecret
{
    public static (string FullKey, string Prefix, byte[] SecretHash) Generate(string marker, int prefixLength)
    {
        var prefix = Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(prefixLength / 2));
        var secret = Base64Url.EncodeToString(RandomNumberGenerator.GetBytes(32));
        return ($"{marker}{prefix}_{secret}", prefix, Hash(secret));
    }

    public static bool TryParse(string marker, int prefixLength, string key, out string prefix, out string secret)
    {
        prefix = secret = string.Empty;
        var minLength = marker.Length + prefixLength + 2;
        if (key.Length < minLength || !key.StartsWith(marker, StringComparison.Ordinal) || key[marker.Length + prefixLength] != '_')
            return false;

        prefix = key.Substring(marker.Length, prefixLength);
        secret = key[(marker.Length + prefixLength + 1)..];
        return true;
    }

    public static byte[] Hash(string secret) => SHA256.HashData(Encoding.UTF8.GetBytes(secret));
}