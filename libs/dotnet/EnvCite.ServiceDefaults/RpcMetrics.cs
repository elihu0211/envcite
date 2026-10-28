using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Text.RegularExpressions;

using Grpc.Core;

namespace EnvCite.ServiceDefaults;

/// <summary>
/// gRPC RED 指標兩側（用戶端 <c>rpc.client.call.duration</c>、伺服器端 <c>rpc.server.call.duration</c>）共用的約定，
/// 與 Python 的 <c>envcite_grpc.metrics</c> 一致，兩側才能用同一組標籤對照。標籤只有服務、方法、結果狀態，
/// 都是有限集合；租戶與案件內容不進指標。
/// </summary>
public static partial class RpcMetrics
{
    /// <summary>呼叫從毫秒到數十秒；預設的直方圖邊界以毫秒為單位，套在秒上會全擠在第一格。</summary>
    public static InstrumentAdvice<double> DurationAdvice { get; } = new()
    {
        HistogramBucketBoundaries = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
    };

    public static TagList Tags(string service, string method, StatusCode status) => new()
    {
        { "rpc.system.name", "grpc" },
        { "rpc.service", service },
        { "rpc.method", method },
        { "rpc.response.status_code", StatusName(status) },
    };

    /// <summary><c>DeadlineExceeded</c> → <c>DEADLINE_EXCEEDED</c>，與 gRPC 規格及 Python 端的狀態名稱一致。</summary>
    public static string StatusName(StatusCode status) => WordBoundary().Replace(status.ToString(), "_$1").ToUpperInvariant();

    [GeneratedRegex("(?<=[a-z])([A-Z])")]
    private static partial Regex WordBoundary();
}