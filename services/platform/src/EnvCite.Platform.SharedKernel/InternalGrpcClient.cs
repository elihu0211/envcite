using Grpc.Core;
using Grpc.Net.Client.Configuration;
using Grpc.Net.ClientFactory;

using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>內部 gRPC 服務的連線設定；各模組以子類別區分（並宣告自己的區段名稱），讓 DI 能同時持有多份。</summary>
public abstract class InternalGrpcClientOptions
{
    /// <summary>
    /// gRPC 位址：叢集內為 <c>dns:///&lt;headless service&gt;:&lt;port&gt;</c>（每個 pod 一條連線），
    /// 本機為 <c>http://localhost:&lt;port&gt;</c>。
    /// </summary>
    public string Address { get; set; } = string.Empty;

    public TimeSpan Timeout { get; set; } = TimeSpan.FromSeconds(5);
}

/// <summary>位址要是絕對 URI、逾時要為正；錯誤訊息指出是哪個設定鍵。</summary>
public sealed class InternalGrpcClientOptionsValidator<TOptions> : IValidateOptions<TOptions>
    where TOptions : InternalGrpcClientOptions, IPlatformOptions
{
    public ValidateOptionsResult Validate(string? name, TOptions options)
    {
        var failures = new List<string>();
        if (!Uri.TryCreate(options.Address, UriKind.Absolute, out _))
        {
            failures.Add($"{TOptions.SectionName}:Address must be an absolute URI.");
        }

        if (options.Timeout <= TimeSpan.Zero)
        {
            failures.Add($"{TOptions.SectionName}:Timeout must be positive.");
        }

        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }
}

public static class InternalGrpcClient
{
    /// <summary>
    /// 註冊一個內部服務的 typed gRPC client，附帶 tenant/request metadata；服務間的認證與授權由網格負責，
    /// 這裡不帶應用層密鑰。位址在用到時才自 <see cref="IOptions{TOptions}"/> 取得，驗證在主機啟動時做。
    /// </summary>
    public static IServiceCollection AddInternalGrpcClient<TClient, TOptions>(this IServiceCollection services)
        where TClient : class
        where TOptions : InternalGrpcClientOptions, IPlatformOptions
    {
        services.AddPlatformOptions<TOptions, InternalGrpcClientOptionsValidator<TOptions>>();
        services.AddHttpContextAccessor();
        services.TryAddTransient<GrpcClientMetrics>();

        services.AddGrpcClient<TClient>((provider, o) =>
                o.Address = new Uri(provider.GetRequiredService<IOptions<TOptions>>().Value.Address))
            .AddInterceptor<GrpcClientMetrics>()
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
            })
            // 長壽命的 HTTP/2 連線：keepalive 偵測 NAT 或負載平衡器的靜默斷線（間隔須大於伺服器容許的
            // 最短 ping 間隔，見 envcite_grpc.connection）；閒置不回收，連線的壽命由伺服器的 GOAWAY 決定。
            .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
            {
                PooledConnectionIdleTimeout = Timeout.InfiniteTimeSpan,
                KeepAlivePingDelay = TimeSpan.FromSeconds(60),
                KeepAlivePingTimeout = TimeSpan.FromSeconds(20),
                KeepAlivePingPolicy = HttpKeepAlivePingPolicy.Always,
                ConnectTimeout = TimeSpan.FromSeconds(10),
                EnableMultipleHttp2Connections = true,
            })
            .ConfigureChannel((provider, channel) =>
            {
                // 一個 channel 就是一條長駐的 HTTP/2 連線，因此 ClusterIP Service 會把每次呼叫
                // 都釘在同一個 pod。使用 `dns:///<headless service>:<port>` 時，resolver 會回傳每個
                // pod，由 round_robin 將呼叫分散到各 pod；單純的 http:// 位址則只有一台主機。
                channel.ServiceConfig = new ServiceConfig
                {
                    LoadBalancingConfigs = { new RoundRobinConfig() },
                    // 重試節流：失敗耗一枚代幣、成功補回 0.1 枚，代幣降到一半以下就不再重試，
                    // 下游整體失靈時重試自己收斂而不是放大流量（數值與 envcite_grpc.connection 一致）。
                    RetryThrottling = new RetryThrottlingPolicy { MaxTokens = 10, TokenRatio = 0.1 },
                    // 內部服務的方法都是唯讀、確定性的一元呼叫（生成不在其中），UNAVAILABLE 重試安全；
                    // 不重試 DEADLINE_EXCEEDED（期限已用盡）與 RESOURCE_EXHAUSTED（容量用盡要立即失敗）。
                    MethodConfigs =
                    {
                        new MethodConfig
                        {
                            Names = { MethodName.Default },
                            RetryPolicy = new RetryPolicy
                            {
                                MaxAttempts = 3,
                                InitialBackoff = TimeSpan.FromMilliseconds(100),
                                MaxBackoff = TimeSpan.FromSeconds(1),
                                BackoffMultiplier = 2,
                                RetryableStatusCodes = { StatusCode.Unavailable },
                            },
                        },
                    },
                };

                // 叢集網路內使用明文 h2c；要用 TLS 就把 Address 改為 https。
                var address = new Uri(provider.GetRequiredService<IOptions<TOptions>>().Value.Address);
                if (address.Scheme != Uri.UriSchemeHttps)
                {
                    channel.Credentials = ChannelCredentials.Insecure;
                    channel.UnsafeUseInsecureChannelCallCredentials = true;
                }
            });

        return services;
    }
}