using System.Globalization;

using EnvCite.ServiceDefaults;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// <c>Platform:Brand</c> 區段：系統名稱的唯一來源。介面標頭與頁面標題、信件主旨與內文、命令列的測試信、
/// OpenAPI 描述與寄件者顯示名稱（<see cref="MailOptions.FromName"/> 沒設時）都讀這裡，改名只改這一個鍵。
/// 圖檔標誌（<c>wwwroot/brand/*.svg</c>）與文件不讀設定，改名時另外換。
/// </summary>
public sealed class BrandOptions : ISectionOptions
{
    /// <summary>預設的系統名稱；英文介面對它有對照（<c>EnvCite</c>），自訂名稱則原樣顯示。</summary>
    public const string DefaultName = "環境知識";

    /// <summary>
    /// 名稱的字元數上限。寄件者顯示名稱在手機信件清單上會被截斷，太長的名稱使用者看不到完整的名字；
    /// 以使用者看到的字（文字單元）計，不以 UTF-16 碼元計。
    /// </summary>
    public const int MaxNameLength = 25;

    public static string SectionName => "Platform:Brand";

    public string Name { get; set; } = DefaultName;
}

internal sealed class BrandOptionsValidator : IValidateOptions<BrandOptions>
{
    public ValidateOptionsResult Validate(string? name, BrandOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.Name))
        {
            return ValidateOptionsResult.Fail("Platform:Brand:Name must not be blank.");
        }

        // 名稱會進信件的標頭（主旨、寄件者）：換行或其他控制字元不是名稱的一部分。
        if (options.Name.Any(char.IsControl))
        {
            return ValidateOptionsResult.Fail("Platform:Brand:Name must not contain control characters.");
        }

        return new StringInfo(options.Name).LengthInTextElements > BrandOptions.MaxNameLength
            ? ValidateOptionsResult.Fail($"Platform:Brand:Name must be at most {BrandOptions.MaxNameLength} characters.")
            : ValidateOptionsResult.Success;
    }
}

public static class BrandServiceCollectionExtensions
{
    /// <summary>註冊 <c>Platform:Brand</c>；每個讀它的模組各自呼叫即可，驗證規則只註冊一次。</summary>
    public static IServiceCollection AddPlatformBrand(this IServiceCollection services)
    {
        services.AddSectionOptions<BrandOptions, BrandOptionsValidator>();
        return services;
    }
}