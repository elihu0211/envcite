using EnvCite.Platform.SharedKernel;
using EnvCite.ServiceDefaults;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Data;

public static class PlatformDataModule
{
    /// <summary>註冊 <see cref="PlatformDbContext"/>。</summary>
    public static IServiceCollection AddPlatformData(this IServiceCollection services)
    {
        services.AddSectionOptions<DatabaseOptions, DatabaseOptionsValidator>();
        services.AddDbContext<PlatformDbContext>((provider, options) =>
            Configure(options, provider.GetRequiredService<IOptions<DatabaseOptions>>().Value.Platform));
        return services;
    }

    /// <summary>
    /// 應用程式與 design-time factory（遷移 bundle）共用：遷移歷史表的位置兩邊必須一致，
    /// 否則 bundle 會對著另一張歷史表判斷哪些遷移已套用。
    /// </summary>
    internal static void Configure(DbContextOptionsBuilder options, string connectionString) =>
        options.UseNpgsql(
            connectionString,
            npgsql => npgsql.MigrationsHistoryTable("__EFMigrationsHistory", PlatformDbContext.Schema));
}