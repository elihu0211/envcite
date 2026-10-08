using EnvLaw.Consult.V2;
using EnvLaw.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Consult;

/// <summary>env-law-consult 的連線設定（<c>Consult</c> 區段；叢集內為 <c>dns:///env-law-consult:50052</c>）。</summary>
public sealed class ConsultOptions : InternalGrpcClientOptions, IPlatformOptions
{
    public static string SectionName => "Consult";
}

public static class ConsultModule
{
    public static IServiceCollection AddConsultModule(this IServiceCollection services)
    {
        services.AddInternalGrpcClient<ConsultationService.ConsultationServiceClient, ConsultOptions>();
        return services;
    }
}