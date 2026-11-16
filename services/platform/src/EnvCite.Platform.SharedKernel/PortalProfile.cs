using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>目前登入成員的個人資料摘要（從資料庫讀取，非 cookie claim）。</summary>
public sealed record PortalProfileInfo(string Email, string? DisplayName, string Role, bool TwoFactorEnabled, bool Managed, int AvatarVersion = 0);

/// <summary>成員大頭貼的影像與快取版本。</summary>
public sealed record PortalAvatar(byte[] Data, string ContentType, int Version);

/// <summary>客戶入口成員的個人資料管理（目前請求的租戶內）。</summary>
public interface IPortalProfile
{
    Task<PortalProfileInfo?> GetAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>更新自己的顯示名稱（空白代表清除）。由目錄同步管理的成員不得自改。</summary>
    Task<Outcome<string?>> UpdateDisplayNameAsync(ClaimsPrincipal user, string? displayName, CancellationToken cancellationToken);

    /// <summary>上傳並正規化大頭貼（裁切為 256×256 WebP、剝除中繼資料、更新 cookie 與稽核）。</summary>
    Task<Outcome<int>> UpdateAvatarAsync(ClaimsPrincipal user, Stream imageStream, long contentLength, CancellationToken cancellationToken);

    /// <summary>移除大頭貼，退回預設首字母徽章。</summary>
    Task<Outcome<int>> RemoveAvatarAsync(ClaimsPrincipal user, CancellationToken cancellationToken);

    /// <summary>讀取目前租戶內指定成員的大頭貼；未設定或不存在則 null。</summary>
    Task<PortalAvatar?> GetAvatarAsync(string userId, CancellationToken cancellationToken);
}