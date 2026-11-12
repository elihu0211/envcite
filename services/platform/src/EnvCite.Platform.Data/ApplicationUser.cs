using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Data;

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

    /// <summary>
    /// 停用：不能登入，既有的 session 也立刻失效（停用時會換安全戳記，session 驗證也直接檢查這個旗標）。
    /// 帳號與稽核紀錄都保留：離職的人不是被刪掉，只是進不來。
    /// </summary>
    public bool Disabled { get; set; }

    /// <summary>
    /// 身分來源（目錄同步）給這個人的穩定 Id，租戶內唯一；沒連結則 null。email 會變，這個不會，所以是同步比對的首要鍵。
    /// </summary>
    public string? ExternalId { get; set; }

    /// <summary>
    /// 這位成員由誰管理：null 是人，<c>directory</c> 是目錄同步。受管成員的角色與停用由身分來源決定，
    /// 介面與管理動作鎖住（否則下一輪同步就悄悄蓋掉人的修改）。
    /// </summary>
    public string? ManagedBy { get; set; }

    /// <summary>目錄同步最後一次改動這位成員的時間（沒有變化的重送不更新）。</summary>
    public DateTimeOffset? SyncedAt { get; set; }
}