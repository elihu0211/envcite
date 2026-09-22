using EnvLaw.Ontology.V1;
using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Ontology;

public sealed class OntologyOptions
{
    /// <summary>gRPC address of env-law-ontology, e.g. <c>http://ontology:50051</c>.</summary>
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
            // Plain h2c inside the cluster network; switch Address to https to use TLS.
            .ConfigureChannel(channel => channel.UnsafeUseInsecureChannelCallCredentials = address.Scheme == Uri.UriSchemeHttp);

        return services;
    }
}