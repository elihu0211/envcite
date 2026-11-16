using System.Diagnostics;

using EnvCite.ServiceDefaults;

using Finbuckle.MultiTenant.Abstractions;

using Grpc.Core;

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

    /// <summary>統一將上游 gRPC 的 <see cref="RpcException"/> 對映為 <see cref="AdminOutcome{T}"/>。</summary>
    public static Task<AdminOutcome<T>> CallAsync<T>(string unavailableCode, Func<Task<T>> call) =>
        CallAsync(unavailableCode, unavailableCode, call);

    /// <summary>統一將上游 gRPC 的 <see cref="RpcException"/> 對映為 <see cref="AdminOutcome{T}"/>（可區分容量用盡代碼）。</summary>
    public static async Task<AdminOutcome<T>> CallAsync<T>(string unavailableCode, string exhaustedCode, Func<Task<T>> call)
    {
        try
        {
            return AdminOutcome.Ok(await call());
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.NotFound)
        {
            return AdminOutcome.NotFound<T>(OutcomeCodes.LawNotFound);
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.InvalidArgument)
        {
            return AdminOutcome.Invalid<T>(OutcomeCodes.UpstreamInvalidRequest, ex.Status.Detail);
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.ResourceExhausted)
        {
            return AdminOutcome.Unavailable<T>(exhaustedCode);
        }
        catch (RpcException ex) when (ex.StatusCode is StatusCode.Unavailable or StatusCode.DeadlineExceeded)
        {
            return AdminOutcome.Unavailable<T>(unavailableCode);
        }
    }
}