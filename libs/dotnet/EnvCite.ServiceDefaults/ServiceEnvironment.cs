using System.Collections;

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Configuration.EnvironmentVariables;
using Microsoft.Extensions.Configuration.Memory;

namespace EnvCite.ServiceDefaults;

/// <summary>
/// 環境變數進設定的方式，各服務同一個約定：自訂變數一律 <c>ENVCITE_</c> 加設定路徑（<c>:</c> 寫成 <c>__</c>），
/// 如 <c>ENVCITE_Platform__DefaultPlan</c>；不帶前綴的環境變數不進設定。
/// 唯一的例外是 <c>OTEL_*</c>：它是 OpenTelemetry 的標準名稱，鍵名原樣保留，OpenTelemetry 才讀得到。
/// </summary>
public static class ServiceEnvironment
{
    public const string Prefix = "ENVCITE_";

    /// <summary>
    /// 以前綴過濾的環境變數取代預設的環境變數來源，位置不動（仍高於 appsettings、低於命令列）。
    /// </summary>
    public static ConfigurationManager AddServiceEnvironment(this ConfigurationManager configuration) =>
        configuration.AddServiceEnvironment(Environment.GetEnvironmentVariables());

    /// <summary>環境由參數給，供測試注入。</summary>
    public static ConfigurationManager AddServiceEnvironment(this ConfigurationManager configuration, IDictionary environment)
    {
        var at = configuration.Sources.Count;
        foreach (var unprefixed in configuration.Sources
                     .OfType<EnvironmentVariablesConfigurationSource>()
                     .Where(s => string.IsNullOrEmpty(s.Prefix))
                     .ToList())
        {
            at = configuration.Sources.IndexOf(unprefixed);
            configuration.Sources.Remove(unprefixed);
        }

        var data = new Dictionary<string, string?>();
        foreach (DictionaryEntry entry in environment)
        {
            var name = (string)entry.Key;
            if (name.StartsWith(Prefix, StringComparison.OrdinalIgnoreCase))
                data[name[Prefix.Length..].Replace("__", ":", StringComparison.Ordinal)] = entry.Value?.ToString();
            else if (name.StartsWith("OTEL_", StringComparison.Ordinal))
                data[name] = entry.Value?.ToString();
        }

        configuration.Sources.Insert(at, new MemoryConfigurationSource { InitialData = data });
        return configuration;
    }
}