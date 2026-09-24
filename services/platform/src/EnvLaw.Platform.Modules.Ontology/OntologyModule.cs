using EnvLaw.Ontology.V1;
using EnvLaw.Platform.SharedKernel;

using Grpc.Core;
using Grpc.Net.Client.Configuration;

using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Ontology;

public sealed class OntologyOptions
{
    /// <summary>
    /// env-law-ontology 的 gRPC 位址：叢集內為 <c>dns:///env-law-ontology:50051</c>
    /// （headless Service，每個 pod 一條連線），本機為 <c>http://localhost:50051</c>。
    /// </summary>
    public string Address { get; set; } = string.Empty;

    /// <summary>以 <c>authorization: Bearer …</c> 送出的共用密鑰（即該服務的 ONTOLOGY_INTERNAL_TOKEN）。</summary>
    public string InternalToken { get; set; } = string.Empty;

    public TimeSpan Timeout { get; set; } = TimeSpan.FromSeconds(5);
}

public static class OntologyModule
{
    /// <summary>註冊 env-law-ontology 的 typed gRPC client，附帶內部驗證與 tenant/request metadata。</summary>
    public static IServiceCollection AddOntologyModule(this IServiceCollection services, IConfiguration section)
    {
        var options = section.Get<OntologyOptions>() ?? new OntologyOptions();
        if (!Uri.TryCreate(options.Address, UriKind.Absolute, out var address))
        {
            throw new InvalidOperationException("Ontology:Address must be an absolute URI.");
        }

        if (string.IsNullOrEmpty(options.InternalToken))
        {
            throw new InvalidOperationException("Ontology:InternalToken is not configured.");
        }

        services.AddHttpContextAccessor();
        services.AddSingleton(options);

        services.AddGrpcClient<OntologyService.OntologyServiceClient>(o => o.Address = address)
            .AddCallCredentials((_, metadata, provider) =>
            {
                metadata.Add("authorization", $"Bearer {options.InternalToken}");
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
            .ConfigureChannel(channel =>
            {
                // 一個 channel 就是一條長駐的 HTTP/2 連線，因此 ClusterIP Service 會把每次呼叫
                // 都釘在同一個 pod。使用 `dns:///<headless service>:<port>` 時，resolver 會回傳每個
                // pod，由 round_robin 將呼叫分散到各 pod；單純的 http:// 位址則只有一台主機。
                channel.ServiceConfig = new ServiceConfig { LoadBalancingConfigs = { new RoundRobinConfig() } };

                // 叢集網路內使用明文 h2c；要用 TLS 就把 Address 改為 https。
                if (address.Scheme != Uri.UriSchemeHttps)
                {
                    channel.Credentials = ChannelCredentials.Insecure;
                    channel.UnsafeUseInsecureChannelCallCredentials = true;
                }
            });

        return services;
    }
}