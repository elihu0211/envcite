using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;

namespace EnvCite.Platform.Data;

/// <summary>
/// 一個綁好租戶的 DI scope：在它裡面解析的 <c>UserManager</c>、<c>PlatformDbContext</c> 等只看得到這個租戶的資料。
/// 只能從 <see cref="TenantScopes.InAsync{T}"/> 的委派裡拿到，離開委派 scope 就處置。
/// 這是「在指定租戶裡取服務」唯一被允許的閘門，所以服務定位集中在這裡，不散在各個類別。
/// </summary>
public sealed class TenantScope(TenantInfo tenant, AsyncServiceScope scope)
{
    public TenantInfo Tenant => tenant;

    /// <summary>在這個租戶的 scope 裡解析服務。</summary>
    public T Get<T>() where T : notnull => scope.ServiceProvider.GetRequiredService<T>();

    /// <summary>
    /// 明確繫結到目標租戶的 <see cref="PlatformDbContext"/>（不靠 ambient），一次操作一個，呼叫端 <c>await using</c>。
    /// 稽核事件要與變更同一個 <c>SaveChanges</c> 時用這個。
    /// </summary>
    public PlatformDbContext CreateDb() => MultiTenantDbContext.Create<PlatformDbContext, TenantInfo>(tenant, scope.ServiceProvider);
}

/// <summary>
/// 在指定租戶裡執行一個操作。營運者與互動式 circuit 的呼叫沒有 ambient tenant（租戶 context 是 AsyncLocal，
/// 只有經過租戶解析的 HTTP 請求才有值），所以每個操作自己開一個 scope、把租戶設成目標租戶，用完就丟：
/// 一個操作一個 <c>DbContext</c>，也是微軟對 Blazor 伺服器端使用 EF Core 的建議。
/// </summary>
public sealed class TenantScopes(IServiceScopeFactory scopes)
{
    /// <summary>
    /// 找不到租戶回 <see cref="OutcomeCodes.TenantNotFound"/>；找到就開 scope、綁定、執行 <paramref name="operation"/>。
    /// 綁定與 <paramref name="operation"/> 在同一個 async 流程裡，租戶 context 才會傳到它裡面的每一個 await；
    /// 不要把 <see cref="TenantScope"/> 傳出委派之外，也不要在別的 async 輔助方法裡另外綁定（方法一返回，值就丟掉）。
    /// </summary>
    public Task<AdminOutcome<T>> InAsync<T>(string tenantId, Func<TenantScope, Task<AdminOutcome<T>>> operation) =>
        RunAsync(store => store.GetAsync(tenantId), operation);

    /// <summary>
    /// 同 <see cref="InAsync{T}"/>，但租戶以識別碼（網址與登入 cookie 裡的那個，如 <c>acme</c>）而不是內部 Id 指定。
    /// 互動式 circuit 用它：身分裡只有租戶識別碼。
    /// </summary>
    public Task<AdminOutcome<T>> InByIdentifierAsync<T>(string tenantIdentifier, Func<TenantScope, Task<AdminOutcome<T>>> operation) =>
        RunAsync(store => store.GetByIdentifierAsync(tenantIdentifier), operation);

    private async Task<AdminOutcome<T>> RunAsync<T>(
        Func<IMultiTenantStore<TenantInfo>, Task<TenantInfo?>> find, Func<TenantScope, Task<AdminOutcome<T>>> operation)
    {
        await using var scope = scopes.CreateAsyncScope();
        if (await find(scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>()) is not { } tenant)
            return AdminOutcome.NotFound<T>(OutcomeCodes.TenantNotFound);

        scope.ServiceProvider.GetRequiredService<IMultiTenantContextSetter>().MultiTenantContext = new MultiTenantContext<TenantInfo>(tenant);
        return await operation(new TenantScope(tenant, scope));
    }
}