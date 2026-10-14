using EnvCite.Ontology.V1;
using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>envcite-ontology 的連線設定（<c>Ontology</c> 區段；叢集內為 <c>dns:///envcite-ontology:50051</c>）。</summary>
public sealed class OntologyOptions : InternalGrpcClientOptions, IPlatformOptions
{
    public static string SectionName => "Ontology";

    /// <summary>推論的逾時：要把某日全部有效的陳述 ground 並求解，比查一條法規久得多。</summary>
    public TimeSpan ReasoningTimeout { get; set; } = TimeSpan.FromSeconds(30);
}

public static class OntologyModule
{
    public static IServiceCollection AddOntologyModule(this IServiceCollection services)
    {
        services.AddInternalGrpcClient<OntologyService.OntologyServiceClient, OntologyOptions>();
        services.AddInternalGrpcClient<ReasoningService.ReasoningServiceClient, OntologyOptions>();
        services.AddScoped<ILawSearch, LawSearch>();
        return services;
    }
}