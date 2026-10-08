using EnvLaw.Ontology.V1;
using EnvLaw.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Ontology;

/// <summary>env-law-ontology 的連線設定（<c>Ontology</c> 區段；叢集內為 <c>dns:///env-law-ontology:50051</c>）。</summary>
public sealed class OntologyOptions : InternalGrpcClientOptions, IPlatformOptions
{
    public static string SectionName => "Ontology";
}

public static class OntologyModule
{
    public static IServiceCollection AddOntologyModule(this IServiceCollection services)
    {
        services.AddInternalGrpcClient<OntologyService.OntologyServiceClient, OntologyOptions>();
        return services;
    }
}