using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>租戶擁有者的組織設定（目前請求的租戶內）。</summary>
public interface ITenantSelfService
{
    /// <summary>修改目前租戶的顯示名稱（識別碼不變）；只限資料庫中角色為擁有者的成員。</summary>
    Task<Outcome<PortalTenant>> RenameAsync(ClaimsPrincipal actor, string name, CancellationToken cancellationToken);
}