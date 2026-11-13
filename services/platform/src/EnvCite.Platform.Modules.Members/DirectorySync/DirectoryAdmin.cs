using System.Security.Claims;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 營運者看租戶的目錄同步，並在出事時關掉或解除管理。營運者的請求沒有 ambient tenant，所以每個操作經
/// <see cref="TenantScopes"/> 開一個綁好目標租戶的 scope（做法同 <see cref="MemberAdmin"/>）。
/// 設定與 token 不在這裡：由客戶的擁有者管理。
/// </summary>
internal sealed class DirectoryAdmin(TenantScopes tenants) : IDirectoryAdmin
{
    public Task<AdminOutcome<DirectoryStatus>> GetStatusAsync(string tenantId, CancellationToken cancellationToken) =>
        tenants.InAsync<DirectoryStatus>(tenantId, scope => scope.Get<DirectoryManagement>().StatusAsync(cancellationToken));

    public Task<AdminOutcome<DirectoryStatus>> DisableAsync(ClaimsPrincipal actor, string tenantId, CancellationToken cancellationToken) =>
        tenants.InAsync<DirectoryStatus>(tenantId, scope => scope.Get<DirectoryManagement>().DisableAsync(actor, cancellationToken));

    public Task<AdminOutcome<MemberInfo>> ReleaseManagedAsync(ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken) =>
        tenants.InAsync<MemberInfo>(tenantId, scope => scope.Get<DirectoryManagement>().ReleaseManagedAsync(actor, userId, cancellationToken));
}