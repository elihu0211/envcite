using EnvCite.Platform.Modules.Tenancy;

namespace EnvCite.Platform.Api;

/// <summary>主機建好之後、開始接請求（或跑命令列）之前的啟動工作。順序固定：遷移，再種子資料。</summary>
internal static class PlatformStartup
{
    /// <summary>
    /// 套用遷移：<c>tenancy</c> 先、<c>platform</c> 後（租戶 store 是租戶資料的前提）。只給 Development 與測試；
    /// 正式環境用遷移 bundle 在 Job 中套用。
    /// </summary>
    public static async Task MigrateAsync(WebApplication app)
    {
        await using var scope = app.Services.CreateAsyncScope();
        await scope.ServiceProvider.GetRequiredService<TenancyDbContext>().Database.MigrateAsync();
        await scope.ServiceProvider.GetRequiredService<PlatformDbContext>().Database.MigrateAsync();
    }

    /// <summary>本機的第一組資料：一次性密碼只印在終端機，不寫進日誌。</summary>
    public static async Task SeedDevelopmentDataAsync(WebApplication app)
    {
        if (await DevelopmentSeed.RunAsync(app.Services, CancellationToken.None) is { } seeded)
            Console.WriteLine(DevelopmentSeed.Describe(seeded));

        if (await DevelopmentSeed.RunOperatorAsync(app.Services, CancellationToken.None) is { } seededOperator)
            Console.WriteLine(DevelopmentSeed.Describe(seededOperator));
    }
}