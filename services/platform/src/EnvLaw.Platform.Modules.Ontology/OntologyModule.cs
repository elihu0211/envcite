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
    /// gRPC address of env-law-ontology: <c>dns:///env-law-ontology:50051</c> in the cluster
    /// (headless Service, one connection per pod), <c>http://localhost:50051</c> locally.
    /// </summary>
    public string Address { get; set; } = string.Empty;

    /// <summary>Shared secret sent as <c>authorization: Bearer …</c> (the service's ONTOLOGY_INTERNAL_TOKEN).</summary>
    public string InternalToken { get; set; } = string.Empty;

    public TimeSpan Timeout { get; set; } = TimeSpan.FromSeconds(5);
}

public static class OntologyModule
{
    /// <summary>Registers the typed gRPC client for env-law-ontology with internal auth and tenant/request metadata.</summary>
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
                // A channel is one long-lived HTTP/2 connection, so a ClusterIP Service pins every
                // call to one pod. With `dns:///<headless service>:<port>` the resolver returns every
                // pod and round_robin spreads calls across them; a plain http:// address is one host.
                channel.ServiceConfig = new ServiceConfig { LoadBalancingConfigs = { new RoundRobinConfig() } };

                // Plain h2c inside the cluster network; switch Address to https to use TLS.
                if (address.Scheme != Uri.UriSchemeHttps)
                {
                    channel.Credentials = ChannelCredentials.Insecure;
                    channel.UnsafeUseInsecureChannelCallCredentials = true;
                }
            });

        return services;
    }
}