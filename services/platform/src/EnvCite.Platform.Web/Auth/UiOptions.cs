using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// <c>Platform:Ui</c> 區段。營運者後台是可選功能，預設關閉（與 <c>Platform:Portal:Enabled</c> 對稱）；Development 一律開著。
/// 沒開就不掛 <c>/ui</c>（回 404），API 照常啟動。後台的帳號存在資料庫（營運者金鑰的 <c>/admin/operators</c> 管理），
/// 不需要任何外部憑證，所以開關只是產品決定，不是 Secret 到位與否。
/// </summary>
public sealed class UiOptions : IPlatformOptions
{
    public static string SectionName => "Platform:Ui";

    public bool Enabled { get; set; }

    /// <summary>
    /// 同一個來源位址每分鐘最多送幾次登入、第二步驗證或改密碼（成功與失敗都算）。帳號鎖定只擋「同一個帳號被猜」，
    /// 擋不了一個來源在試很多帳號，這個限制才是。營運者只有少數幾位，預設比客戶入口低。
    /// </summary>
    public int SignInAttemptsPerMinute { get; set; } = 20;

    /// <summary>
    /// 後台 session 從登入起算的絕對上限。cookie 本身會滑動續期（閒置 8 小時才過期），沒有這個上限，
    /// 被竊的 cookie 只要持續使用就永遠有效；超過上限一律要重新登入，續期也延不了它。
    /// </summary>
    public TimeSpan SessionMaxAge { get; set; } = TimeSpan.FromHours(12);
}

internal sealed class UiOptionsValidator : IValidateOptions<UiOptions>
{
    public ValidateOptionsResult Validate(string? name, UiOptions options)
    {
        if (options.SignInAttemptsPerMinute is < 1 or > 1000)
        {
            return ValidateOptionsResult.Fail("Platform:Ui:SignInAttemptsPerMinute must be between 1 and 1000.");
        }

        return options.SessionMaxAge < TimeSpan.FromMinutes(1) || options.SessionMaxAge > TimeSpan.FromDays(7)
            ? ValidateOptionsResult.Fail("Platform:Ui:SessionMaxAge must be between 1 minute and 7 days.")
            : ValidateOptionsResult.Success;
    }
}