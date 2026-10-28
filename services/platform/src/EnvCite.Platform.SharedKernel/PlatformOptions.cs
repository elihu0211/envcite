using System.ComponentModel.DataAnnotations;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 一份由設定檔、環境變數（k8s 的 ConfigMap 與 Secret）繫結的設定。型別自己宣告區段名稱，
/// 讀設定的地方只剩一個：<see cref="PlatformOptionsExtensions.AddPlatformOptions{TOptions}"/>。
/// </summary>
public interface IPlatformOptions
{
    static abstract string SectionName { get; }
}

public static class PlatformOptionsExtensions
{
    /// <summary>
    /// 繫結區段並在<b>主機啟動時</b>驗證：設定缺漏或錯誤，pod 起不來（k8s 看到的是 CrashLoopBackOff
    /// 與明確的訊息），而不是等到第一個請求才在執行中失敗。有驗證規則的設定用
    /// <see cref="AddPlatformOptions{TOptions, TValidator}"/>，設定與它的規則一起註冊。
    /// </summary>
    public static OptionsBuilder<TOptions> AddPlatformOptions<TOptions>(this IServiceCollection services)
        where TOptions : class, IPlatformOptions =>
        services.AddOptions<TOptions>()
            .BindConfiguration(TOptions.SectionName)
            .ValidateOnStart();

    /// <summary>
    /// 繫結並註冊驗證規則。規則以 <c>TryAddEnumerable</c> 註冊：多個模組要同一份設定時（例如資料庫連線）
    /// 各自呼叫即可，規則只註冊一次。
    /// </summary>
    public static OptionsBuilder<TOptions> AddPlatformOptions<TOptions, TValidator>(this IServiceCollection services)
        where TOptions : class, IPlatformOptions
        where TValidator : class, IValidateOptions<TOptions>
    {
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IValidateOptions<TOptions>, TValidator>());
        return services.AddPlatformOptions<TOptions>();
    }
}

/// <summary>資料庫連線（<c>ConnectionStrings</c> 區段；Npgsql 另外讀 <c>PGUSER</c>／<c>PGPASSWORD</c>，密碼可不放在字串裡）。</summary>
public sealed class DatabaseOptions : IPlatformOptions
{
    public static string SectionName => "ConnectionStrings";

    [Required(AllowEmptyStrings = false)]
    public string Platform { get; set; } = string.Empty;
}

[OptionsValidator]
public sealed partial class DatabaseOptionsValidator : IValidateOptions<DatabaseOptions>;