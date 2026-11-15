using EnvCite.Canon.V1;
using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Modules.Canon;

/// <summary>canon 的連線設定（<c>Canon</c> 區段；叢集內為 <c>dns:///canon:50051</c>）。</summary>
public sealed class CanonOptions : InternalGrpcClientOptions, ISectionOptions
{
    public static string SectionName => "Canon";

    /// <summary>推論的逾時：要把某日全部有效的陳述 ground 並求解，比查一條法規久得多。</summary>
    public TimeSpan ReasoningTimeout { get; set; } = TimeSpan.FromSeconds(30);
}

public static class CanonModule
{
    public static IServiceCollection AddCanonModule(this IServiceCollection services)
    {
        services.AddPlatformGrpcClient<CanonService.CanonServiceClient, CanonOptions>();
        services.AddPlatformGrpcClient<ReasoningService.ReasoningServiceClient, CanonOptions>();
        services.AddSectionOptions<RelevanceFloorsOptions, RelevanceFloorsOptionsValidator>();
        services.AddSectionOptions<LawSearchOptions, LawSearchOptionsValidator>();
        services.AddMemoryCache();
        services.AddScoped<ILawSearch, LawSearch>();
        services.AddScoped<CanonGateway>();
        services.AddSingleton<ILawCorpusService, LawCorpusService>();
        services.AddSingleton<ICitationArticleService, CitationArticleService>();
        services.AddSingleton<INormativeReasoningService, NormativeReasoningService>();
        return services;
    }
}