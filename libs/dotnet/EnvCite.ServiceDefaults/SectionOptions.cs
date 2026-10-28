using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace EnvCite.ServiceDefaults;

/// <summary>
/// 一份由設定檔、環境變數（k8s 的 ConfigMap 與 Secret）繫結的設定。型別自己宣告區段名稱，
/// 讀設定的地方只剩一個：<see cref="SectionOptionsExtensions.AddSectionOptions{TOptions}"/>。
/// </summary>
public interface ISectionOptions
{
    static abstract string SectionName { get; }
}

public static class SectionOptionsExtensions
{
    /// <summary>
    /// 繫結區段並在<b>主機啟動時</b>驗證：設定缺漏或錯誤，pod 起不來（k8s 看到的是 CrashLoopBackOff
    /// 與明確的訊息），而不是等到第一個請求才在執行中失敗。有驗證規則的設定用
    /// <see cref="AddSectionOptions{TOptions, TValidator}"/>，設定與它的規則一起註冊。
    /// </summary>
    public static OptionsBuilder<TOptions> AddSectionOptions<TOptions>(this IServiceCollection services)
        where TOptions : class, ISectionOptions =>
        services.AddOptions<TOptions>()
            .BindConfiguration(TOptions.SectionName)
            .ValidateOnStart();

    /// <summary>
    /// 繫結並註冊驗證規則。規則以 <c>TryAddEnumerable</c> 註冊：多個模組要同一份設定時（例如資料庫連線）
    /// 各自呼叫即可，規則只註冊一次。
    /// </summary>
    public static OptionsBuilder<TOptions> AddSectionOptions<TOptions, TValidator>(this IServiceCollection services)
        where TOptions : class, ISectionOptions
        where TValidator : class, IValidateOptions<TOptions>
    {
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IValidateOptions<TOptions>, TValidator>());
        return services.AddSectionOptions<TOptions>();
    }
}