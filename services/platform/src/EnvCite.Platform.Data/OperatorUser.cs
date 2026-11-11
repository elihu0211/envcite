using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Data;

/// <summary>
/// 營運者後台的使用者：ASP.NET Core Identity，與客戶入口的 <see cref="ApplicationUser"/> 同一套機制，
/// 但<b>另一個 user store</b>——營運者不屬於任何租戶，所以不放進隨 Finbuckle 分租戶的成員表（沒有 <c>IsMultiTenant()</c>，
/// 沒有租戶篩選；理由同 <see cref="AuditEvent"/>）。同一個 email 只有一個帳號；登入名稱就是 email。
/// 沒有自助註冊：帳號由已被信任的營運者或系統管理者建立（<c>IOperatorAdmin</c>）。
/// </summary>
/// <remarks>
/// 主鍵是 <see cref="Guid"/>，不是成員的字串：Identity 的 EF store 要 claim／login／token 三個型別，而
/// <c>IdentityUserClaim&lt;string&gt;</c> 等已經是成員表的實體（外鍵指向 <c>ApplicationUser</c>，還被 Finbuckle 當成租戶實體），
/// 不能繼承它們另開表（EF 會當成同一個階層）。<c>IdentityUserClaim&lt;Guid&gt;</c> 等是另一組封閉型別，天然分開。
/// </remarks>
public sealed class OperatorUser : IdentityUser<Guid>
{
    public OperatorUser() => Id = Guid.CreateVersion7();

    public string? DisplayName { get; set; }

    /// <summary>初始密碼或被重設的密碼是系統管理者經手的，使用者第一次登入必須自己改。為 true 時後台只開放改密碼頁。</summary>
    public bool MustChangePassword { get; set; }

    /// <summary>最近一次被接受的 TOTP 時間步（RFC 6238 §5.2）：同一組六位數碼不能用第二次。語意同 <see cref="ApplicationUser.LastTotpStep"/>。</summary>
    public long LastTotpStep { get; set; }

    /// <summary>
    /// 停用：不能登入，既有的 session 在下一個請求就失效（授權每次重查資料庫）。
    /// 帳號與稽核紀錄都保留：離職的人不是被刪掉，只是進不來。
    /// </summary>
    public bool Disabled { get; set; }
}