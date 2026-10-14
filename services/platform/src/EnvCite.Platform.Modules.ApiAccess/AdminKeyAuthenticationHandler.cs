using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;

using Microsoft.AspNetCore.Authentication;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.ApiAccess;

public sealed class AdminKeyOptions : AuthenticationSchemeOptions
{
    /// <summary>admin key 的十六進位 SHA-256（設定鍵 <c>Platform:AdminKeySha256</c>）。留空即停用 admin 存取。</summary>
    public string AdminKeySha256 { get; set; } = string.Empty;
}

/// <summary>以 <c>X-Admin-Key</c> header 驗證平台營運者。</summary>
public sealed class AdminKeyAuthenticationHandler(
    IOptionsMonitor<AdminKeyOptions> options,
    ILoggerFactory logger,
    UrlEncoder encoder)
    : AuthenticationHandler<AdminKeyOptions>(options, logger, encoder)
{
    public const string HeaderName = "X-Admin-Key";

    protected override Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        string? provided = Request.Headers[HeaderName];
        if (string.IsNullOrEmpty(provided))
        {
            return Task.FromResult(AuthenticateResult.NoResult());
        }

        byte[] expected;
        try
        {
            expected = Convert.FromHexString(Options.AdminKeySha256);
        }
        catch (FormatException)
        {
            return Task.FromResult(AuthenticateResult.Fail("Admin key not configured."));
        }

        var actual = SHA256.HashData(Encoding.UTF8.GetBytes(provided));
        if (expected.Length == 0 || !CryptographicOperations.FixedTimeEquals(expected, actual))
        {
            return Task.FromResult(AuthenticateResult.Fail("Invalid admin key."));
        }

        var principal = new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.Role, "platform-admin")], Scheme.Name));
        return Task.FromResult(AuthenticateResult.Success(new AuthenticationTicket(principal, Scheme.Name)));
    }
}