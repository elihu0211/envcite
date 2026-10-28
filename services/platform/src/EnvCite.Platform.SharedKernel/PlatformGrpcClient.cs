using EnvCite.ServiceDefaults;

using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.SharedKernel;

public static class PlatformGrpcClient
{
    /// <summary>
    /// 註冊一個內部服務的 typed gRPC client（連線、重試與指標見 <see cref="InternalGrpcClient"/>），
    /// 每次呼叫帶上這個請求的 <c>x-request-id</c> 與已驗證的 <c>x-tenant-id</c>。
    /// </summary>
    public static IServiceCollection AddPlatformGrpcClient<TClient, TOptions>(this IServiceCollection services)
        where TClient : class
        where TOptions : InternalGrpcClientOptions, ISectionOptions
    {
        services.AddHttpContextAccessor();
        services.AddInternalGrpcClient<TClient, TOptions>()
            .AddCallCredentials((_, metadata, provider) =>
            {
                var httpContext = provider.GetRequiredService<IHttpContextAccessor>().HttpContext;
                if (httpContext is not null)
                {
                    metadata.Add("x-request-id", httpContext.TraceIdentifier);
                    if (httpContext.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is { } tenant)
                    {
                        metadata.Add("x-tenant-id", tenant);
                    }
                }

                return Task.CompletedTask;
            });
        return services;
    }
}