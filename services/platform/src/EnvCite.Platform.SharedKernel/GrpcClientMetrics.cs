using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Text.RegularExpressions;

using Grpc.Core;
using Grpc.Core.Interceptors;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 內部 gRPC 用戶端的 RED 指標：每個一元呼叫一筆 <c>rpc.client.call.duration</c>（秒）。
/// 耗時含用戶端自己的重試（見 <c>InternalGrpcClient</c>），是呼叫端實際等待的時間。
/// 名稱與標籤對齊 Python 伺服器端的 <c>rpc.server.call.duration</c>（<c>envcite_grpc.metrics</c>），
/// 所以兩側能用同一組標籤對照。標籤只有服務、方法、結果狀態，都是有限集合；租戶與案件內容不進指標。
/// 用戶端工廠的 HttpClient 指標看不出 gRPC 的失敗（gRPC 失敗仍是 HTTP 200），所以另有這個。
/// </summary>
public sealed partial class GrpcClientMetrics : Interceptor
{
    public const string MeterName = "EnvCite.Platform.GrpcClient";

    public const string DurationMetric = "rpc.client.call.duration";

    private static readonly Meter Meter = new(MeterName);

    private static readonly Histogram<double> Duration = Meter.CreateHistogram<double>(
        DurationMetric, unit: "s",
        description: "內部 gRPC 一元呼叫從送出到完成的耗時；rpc.response.status_code 為結果。",
        advice: new InstrumentAdvice<double> { HistogramBucketBoundaries = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30] });

    public override AsyncUnaryCall<TResponse> AsyncUnaryCall<TRequest, TResponse>(
        TRequest request, ClientInterceptorContext<TRequest, TResponse> context, AsyncUnaryCallContinuation<TRequest, TResponse> continuation)
    {
        var started = Stopwatch.GetTimestamp();
        var call = continuation(request, context);
        return new AsyncUnaryCall<TResponse>(
            Observe(call.ResponseAsync, context.Method, started),
            call.ResponseHeadersAsync,
            call.GetStatus,
            call.GetTrailers,
            call.Dispose);
    }

    private static async Task<TResponse> Observe<TRequest, TResponse>(
        Task<TResponse> response, Method<TRequest, TResponse> method, long started)
    {
        var status = StatusCode.OK;
        try
        {
            return await response.ConfigureAwait(false);
        }
        catch (RpcException ex)
        {
            status = ex.StatusCode;
            throw;
        }
        catch (OperationCanceledException)
        {
            status = StatusCode.Cancelled;
            throw;
        }
        catch
        {
            status = StatusCode.Unknown;
            throw;
        }
        finally
        {
            Duration.Record(
                Stopwatch.GetElapsedTime(started).TotalSeconds,
                new KeyValuePair<string, object?>("rpc.system.name", "grpc"),
                new KeyValuePair<string, object?>("rpc.service", method.ServiceName),
                new KeyValuePair<string, object?>("rpc.method", method.Name),
                new KeyValuePair<string, object?>("rpc.response.status_code", StatusName(status)));
        }
    }

    /// <summary><c>DeadlineExceeded</c> → <c>DEADLINE_EXCEEDED</c>，與 gRPC 規格及 Python 端的狀態名稱一致。</summary>
    private static string StatusName(StatusCode status) => WordBoundary().Replace(status.ToString(), "_$1").ToUpperInvariant();

    [GeneratedRegex("(?<=[a-z])([A-Z])")]
    private static partial Regex WordBoundary();
}