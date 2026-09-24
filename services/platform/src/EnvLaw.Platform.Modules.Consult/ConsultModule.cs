using EnvLaw.Consult.V2;
using EnvLaw.Platform.SharedKernel;

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Consult;

/// <summary>env-law-consult 的連線設定（<c>Consult</c> 區段；叢集內為 <c>dns:///env-law-consult:50052</c>）。</summary>
public sealed class ConsultOptions : InternalGrpcClientOptions;

public static class ConsultModule
{
    public static IServiceCollection AddConsultModule(this IServiceCollection services, IConfigurationSection section)
    {
        services.AddInternalGrpcClient<ConsultationService.ConsultationServiceClient, ConsultOptions>(section);
        return services;
    }
}