using Grpc.Core;
using Grpc.Net.Client.Balancer;
using Grpc.Net.Client.Configuration;
using Grpc.Net.ClientFactory;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvCite.ServiceDefaults;

/// <summary>內部 gRPC 服務的連線設定；每個上游一個子類別（並宣告自己的區段名稱），讓 DI 能同時持有多份。</summary>
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
    where TOptions : InternalGrpcClientOptions, ISectionOptions
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
    /// 註冊一個內部服務的 typed gRPC client：連線、負載平衡、重試與 RED 指標都在這裡，呼叫端只加自己要帶的
    /// metadata 或期限傳遞。服務間的認證與授權由網格負責，這裡不帶應用層密鑰。位址在用到時才自
    /// <see cref="IOptions{TOptions}"/> 取得，驗證在主機啟動時做。
    /// </summary>
    public static IHttpClientBuilder AddInternalGrpcClient<TClient, TOptions>(this IServiceCollection services)
        where TClient : class
        where TOptions : InternalGrpcClientOptions, ISectionOptions
    {
        services.AddSectionOptions<TOptions, InternalGrpcClientOptionsValidator<TOptions>>();
        services.TryAddSingleton<GrpcClientMetrics>();
        // `dns:///` 的 resolver 預設只在連線中斷時重新解析，擴容後的新 pod 要等到斷線才分得到流量。Python 伺服器
        // 以 max_connection_age 的 GOAWAY 促成重連（envcite_grpc.connection），Kestrel 沒有這個設定，所以由用戶端定期解析。
        services.TryAddEnumerable(ServiceDescriptor.Singleton<ResolverFactory, DnsResolverFactory>(
            _ => new DnsResolverFactory(refreshInterval: TimeSpan.FromSeconds(30))));

        return services.AddGrpcClient<TClient>((provider, o) =>
                o.Address = new Uri(provider.GetRequiredService<IOptions<TOptions>>().Value.Address))
            .AddInterceptor<GrpcClientMetrics>()
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

                // 叢集網路內使用明文 h2c；要用 TLS 就把 Address 改為 https。呼叫端以 call credentials
                // 帶 metadata 時（platform 的租戶），明文 channel 要明示允許。
                var address = new Uri(provider.GetRequiredService<IOptions<TOptions>>().Value.Address);
                if (address.Scheme != Uri.UriSchemeHttps)
                {
                    channel.Credentials = ChannelCredentials.Insecure;
                    channel.UnsafeUseInsecureChannelCallCredentials = true;
                }
            });
    }
}