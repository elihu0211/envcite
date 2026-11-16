namespace EnvCite.Platform.SharedKernel;

public sealed record PortalTenant(string Id, string Identifier, string? Name);

/// <summary>
/// 客戶入口的帳密登入。租戶是目前請求的租戶（登入前由路徑解析）；所有動作都在這個租戶內。
/// </summary>
public interface IPortalAccount : IAccountLifecycle
{
    /// <summary>這個請求（由路徑）解析出的租戶；路徑上的租戶不存在則 null，頁面據此回 404。</summary>
    PortalTenant? CurrentTenant { get; }

    /// <summary>
    /// Google 登入的起點要送出的驗證屬性：登入完成後回 <paramref name="callbackUrl"/>，並記下這個流程屬於目前的租戶。
    /// 每次都請使用者選帳號（共用電腦上不靜默沿用上一個人的 Google 登入）。
    /// </summary>
    Microsoft.AspNetCore.Authentication.AuthenticationProperties GoogleChallenge(string callbackUrl);

    /// <summary>
    /// Google 登入的第二段：讀 Google 回來的身分，對應到<b>這個租戶裡已啟用的成員</b>並簽發 session；不建立帳號。
    /// 對應先看（提供者、<c>sub</c>），沒有再以已驗證的 email 找成員並連結；停用、還沒啟用（沒設過密碼或還要改初始密碼）、
    /// 或流程不屬於這個租戶，一律是 <see cref="SignInStatus.Failed"/>。成員有兩步驟驗證時是 <see cref="SignInStatus.RequiresTwoFactor"/>。
    /// </summary>
    Task<SignInStatus> SignInWithGoogleAsync(CancellationToken cancellationToken);
}