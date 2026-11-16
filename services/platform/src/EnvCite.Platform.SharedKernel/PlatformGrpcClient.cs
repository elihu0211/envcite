using System.Diagnostics;

using EnvCite.ServiceDefaults;

using Finbuckle.MultiTenant.Abstractions;

namespace EnvCite.Platform.SharedKernel;

public static class PlatformGrpcClient
{
    /// <summary>
    /// 註冊一個內部服務的 typed gRPC client（連線、重試與指標見 <see cref="InternalGrpcClient"/>），
    /// 每次呼叫帶上目前的 trace id（<c>x-request-id</c>）與 Finbuckle 租戶 context 的 <c>x-tenant-id</c>。
    /// 租戶 context 是 AsyncLocal：HTTP 請求經租戶解析、互動式 circuit 經 <c>TenantScopes</c> 都設同一份，
    /// 所以不讀 <c>HttpContext</c>（circuit 沒有可靠的 <c>HttpContext</c>）。
    /// </summary>
    public static IServiceCollection AddPlatformGrpcClient<TClient, TOptions>(this IServiceCollection services)
        where TClient : class
        where TOptions : InternalGrpcClientOptions, ISectionOptions
    {
        services.AddInternalGrpcClient<TClient, TOptions>()
            .AddCallCredentials((_, metadata, provider) =>
            {
                if (Activity.Current?.TraceId.ToHexString() is { } requestId)
                    metadata.Add("x-request-id", requestId);

                if (provider.GetRequiredService<IMultiTenantContextAccessor<TenantInfo>>().MultiTenantContext?.TenantInfo?.Identifier is { } tenant)
                    metadata.Add("x-tenant-id", tenant);

                return Task.CompletedTask;
            });
        return services;
    }
}