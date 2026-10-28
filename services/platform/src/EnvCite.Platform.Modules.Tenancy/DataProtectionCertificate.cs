using EnvCite.Platform.SharedKernel;
using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Tenancy;

/// <summary>
/// <c>Platform:DataProtection</c> 區段：金鑰環的靜態加密，<b>可選</b>。兩個值都設才加密；都不設維持明文；只設一個啟動就失敗。
/// 密碼只經環境變數（<c>Platform__DataProtection__CertificatePassword</c>），不進檔案。
/// </summary>
public sealed class DataProtectionCertificateOptions : ISectionOptions
{
    public static string SectionName => "Platform:DataProtection";

    /// <summary>PKCS#12（.pfx）檔路徑，內含私鑰。</summary>
    public string? CertificatePath { get; set; }

    public string? CertificatePassword { get; set; }

    internal bool IsConfigured => !string.IsNullOrEmpty(CertificatePath) && !string.IsNullOrEmpty(CertificatePassword);
}

internal sealed class DataProtectionCertificateOptionsValidator : IValidateOptions<DataProtectionCertificateOptions>
{
    public ValidateOptionsResult Validate(string? name, DataProtectionCertificateOptions options)
    {
        var hasPath = !string.IsNullOrEmpty(options.CertificatePath);
        var hasPassword = !string.IsNullOrEmpty(options.CertificatePassword);

        if (hasPath != hasPassword)
        {
            return ValidateOptionsResult.Fail(
                "Platform:DataProtection:CertificatePath and Platform:DataProtection:CertificatePassword must be set together.");
        }

        return hasPath && !File.Exists(options.CertificatePath)
            ? ValidateOptionsResult.Fail("Platform:DataProtection:CertificatePath does not point to an existing file.")
            : ValidateOptionsResult.Success;
    }
}