using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Modules.Audit;

public static class AuditModule
{
    public static IServiceCollection AddAuditModule(this IServiceCollection services)
    {
        services.TryAddSingleton(TimeProvider.System);
        services.AddScoped<IAuditLog, AuditLog>();
        services.AddSingleton<IAuditQuery, AuditQuery>();
        return services;
    }
}