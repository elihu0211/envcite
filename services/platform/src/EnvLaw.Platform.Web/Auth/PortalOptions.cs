using EnvLaw.Platform.SharedKernel;

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
}