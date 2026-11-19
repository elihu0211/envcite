using EnvCite.Platform.Modules.Canon;
using EnvCite.Platform.Modules.Consult;

using Microsoft.AspNetCore.Http.Timeouts;

namespace EnvCite.Platform.Api;

/// <summary>
/// <c>/v2</c> 的請求逾時：上游 gRPC 各有自己的期限，這一道是兜底，罩住期限之外的工作（資料庫查詢、序列化）。
/// 期限取各上游最長的一個再加緩衝：上游先逾時，端點照常把 gRPC 失敗對映成 503；
/// 兜底只在別處卡住時才動，回 504。
/// </summary>
internal static class PlatformRequestTimeouts
{
    public const string ApiPolicy = "PlatformApi";

    /// <summary>加在最長上游期限之後，讓上游的逾時先發生（對映成 503），而不是被兜底搶先成 504。</summary>
    public static readonly TimeSpan Margin = TimeSpan.FromSeconds(10);

    public static IServiceCollection AddPlatformRequestTimeouts(this IServiceCollection services)
    {
        services.AddRequestTimeouts();
        services.AddOptions<RequestTimeoutOptions>()
            .Configure<IOptions<CanonOptions>, IOptions<ConsultOptions>>((timeouts, canon, consult) =>
                timeouts.AddPolicy(ApiPolicy, Longest(canon.Value, consult.Value) + Margin));
        return services;
    }

    /// <summary><c>/v2</c> 會經過的所有上游期限裡最長的一個（連線預設逾時與推論、抽取的專用期限）。</summary>
    public static TimeSpan Longest(CanonOptions canon, ConsultOptions consult) =>
        new[] { canon.Timeout, canon.ReasoningTimeout, canon.ExtractTimeout, consult.Timeout }.Max();
}