using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 信件測試的容器：與正式環境同一個註冊（<see cref="MailServiceCollectionExtensions.AddPlatformMail"/>），只換掉設定的來源。
/// 組信是純運算，不需要伺服器、資料庫或網路；要驗證選項或換掉傳輸的測試，用 <c>configure</c> 在其後覆寫。
/// </summary>
internal static class MailTestServices
{
    public static ServiceProvider Build(Dictionary<string, string?>? settings = null, Action<IServiceCollection>? configure = null)
    {
        var services = new ServiceCollection();
        services.AddSingleton<IConfiguration>(new ConfigurationBuilder().AddInMemoryCollection(settings ?? []).Build());
        services.AddLogging(builder => builder.AddProvider(new CapturingLoggerProvider()));
        services.AddPlatformMail();
        configure?.Invoke(services);
        return services.BuildServiceProvider();
    }

    public static Dictionary<string, string?> With(params (string Key, string Value)[] values) =>
        values.ToDictionary(v => v.Key, v => (string?)v.Value);
}