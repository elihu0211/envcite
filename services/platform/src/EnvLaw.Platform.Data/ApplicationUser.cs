using Microsoft.AspNetCore.Identity;

namespace EnvLaw.Platform.Data;

/// <summary>
/// 客戶入口的使用者；隨 Finbuckle 的 Identity context 屬於單一租戶（同一個 email 在不同租戶是不同帳號，
/// 同一租戶內不能重複）。登入名稱就是 email。
/// </summary>
public sealed class ApplicationUser : IdentityUser
{
    public string? DisplayName { get; set; }

    /// <summary>角色名稱，見 <c>CustomerRoles</c>；進登入 cookie 的 role claim，授權只讀 claim。</summary>
    public string Role { get; set; } = string.Empty;

    /// <summary>
    /// 初始密碼或被營運者重設的密碼是營運者經手的，使用者第一次登入必須自己改。
    /// 為 true 時入口只開放改密碼頁。
    /// </summary>
    public bool MustChangePassword { get; set; }

    /// <summary>
    /// 最近一次被接受的 TOTP 時間步（RFC 6238 §5.2）：只接受比它更新的步，所以同一組六位數碼不能用第二次
    /// （旁觀者在三十秒內看到的碼不能重放）。以 <c>UPDATE … WHERE LastTotpStep &lt; @step</c> 原子更新，
    /// 同時送出的兩個相同請求只有一個會成功。
    /// </summary>
    public long LastTotpStep { get; set; }
}