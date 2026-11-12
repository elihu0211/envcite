using EnvCite.Platform.SharedKernel;
using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// <c>Platform:Directory</c> 區段：目錄同步（客戶的身分來源把成員推進租戶）。預設關閉；關閉時對應路徑回 404、不接受任何 token。
/// 部署者開了功能面之後，各租戶還要自己開連線（資料，不是設定）才生效。
/// </summary>
public sealed class DirectoryOptions : ISectionOptions
{
    public static string SectionName => "Platform:Directory";

    public bool Enabled { get; set; }

    /// <summary>列出成員時，一頁最多幾筆（身分來源要求更多時以此為上限）。</summary>
    public int MaxPageSize { get; set; } = 100;

    /// <summary>每個 token 每分鐘最多幾次請求。</summary>
    public int RequestsPerMinute { get; set; } = 120;

    /// <summary>多久沒收到身分來源的請求，介面就警示連線可能斷了。</summary>
    public TimeSpan StaleAfter { get; set; } = TimeSpan.FromDays(2);
}

internal sealed class DirectoryOptionsValidator : IValidateOptions<DirectoryOptions>
{
    public ValidateOptionsResult Validate(string? name, DirectoryOptions options)
    {
        List<string> failures = [];
        if (options.MaxPageSize is < 1 or > 1000)
        {
            failures.Add("Platform:Directory:MaxPageSize must be between 1 and 1000.");
        }

        if (options.RequestsPerMinute is < 1 or > 10000)
        {
            failures.Add("Platform:Directory:RequestsPerMinute must be between 1 and 10000.");
        }

        if (options.StaleAfter < TimeSpan.FromMinutes(1))
        {
            failures.Add("Platform:Directory:StaleAfter must be at least one minute.");
        }

        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }
}

internal sealed class DirectoryAvailability(IOptions<DirectoryOptions> options) : IDirectoryAvailability
{
    public bool Available => options.Value.Enabled;

    public TimeSpan StaleAfter => options.Value.StaleAfter;
}