using System.Security.Claims;

using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 營運者看租戶的目錄同步，並在出事時關掉或解除管理。營運者的請求沒有 ambient tenant，所以每次自己開 scope，
/// 把租戶設成目標租戶（做法同 <see cref="MemberAdmin"/>）。設定與 token 不在這裡：由客戶的擁有者管理。
/// </summary>
internal sealed class DirectoryAdmin(IServiceScopeFactory scopes) : IDirectoryAdmin
{
    public Task<AdminOutcome<DirectoryStatus>> GetStatusAsync(string tenantId, CancellationToken cancellationToken) =>
        InTenantAsync(tenantId, management => management.StatusAsync(cancellationToken));

    public Task<AdminOutcome<DirectoryStatus>> DisableAsync(ClaimsPrincipal actor, string tenantId, CancellationToken cancellationToken) =>
        InTenantAsync(tenantId, management => management.DisableAsync(actor, cancellationToken));

    public Task<AdminOutcome<MemberInfo>> ReleaseManagedAsync(ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken) =>
        InTenantAsync(tenantId, management => management.ReleaseManagedAsync(actor, userId, cancellationToken));

    private async Task<AdminOutcome<T>> InTenantAsync<T>(string tenantId, Func<DirectoryManagement, Task<AdminOutcome<T>>> action)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>().GetAsync(tenantId) is not { } tenant)
        {
            return AdminOutcome.NotFound<T>(OutcomeCodes.TenantNotFound);
        }

        Bind(scope, tenant);
        return await action(scope.ServiceProvider.GetRequiredService<DirectoryManagement>());
    }

    /// <summary>
    /// 把這個 scope 的租戶設成目標租戶。必須在解析 <c>UserManager</c>／<c>PlatformDbContext</c> 之前，
    /// 而且<b>不能放進 async 方法</b>：租戶 context 是 AsyncLocal，在 async 方法裡設定，方法一返回就丟掉。
    /// </summary>
    private static void Bind(AsyncServiceScope scope, TenantInfo tenant) =>
        scope.ServiceProvider.GetRequiredService<IMultiTenantContextSetter>().MultiTenantContext = new MultiTenantContext<TenantInfo>(tenant);
}