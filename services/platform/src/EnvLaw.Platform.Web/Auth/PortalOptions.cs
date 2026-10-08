using EnvLaw.Platform.SharedKernel;

using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Web.Auth;

/// <summary>
/// <c>Platform:Portal</c> 區段。客戶入口預設關閉：這一版只有帳密（沒有 MFA、外部登入、寄信與邀請）、
/// 初始密碼由營運者經手，是展示與內部試用的程度，不是給正式客戶用的（07-01 §5、§9）。
/// Development 一律開著，方便本機看畫面。
/// </summary>
public sealed class PortalOptions : IPlatformOptions
{
    public static string SectionName => "Platform:Portal";

    public bool Enabled { get; set; }

    /// <summary>
    /// 同一個來源位址每分鐘最多送幾次登入或改密碼（成功與失敗都算）。帳號鎖定只擋「同一個帳號被猜」，
    /// 擋不了一個來源在試很多帳號（撞庫），這個限制才是。預設 60：機關、顧問公司常是整個辦公室共用一個出口位址，
    /// 早上同時上班登入的人不該被擋；它擋的是自動化的猜測，不是正常使用。
    /// </summary>
    public int SignInAttemptsPerMinute { get; set; } = 60;
}

internal sealed class PortalOptionsValidator : IValidateOptions<PortalOptions>
{
    public ValidateOptionsResult Validate(string? name, PortalOptions options) =>
        options.SignInAttemptsPerMinute is < 1 or > 1000
            ? ValidateOptionsResult.Fail("Platform:Portal:SignInAttemptsPerMinute must be between 1 and 1000.")
            : ValidateOptionsResult.Success;
}